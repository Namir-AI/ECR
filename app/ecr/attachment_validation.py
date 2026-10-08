"""Content-based evidence validation, independent of filenames/browser MIME."""

import json
import re
import warnings
import zlib
from dataclasses import dataclass
from io import BytesIO
from tempfile import TemporaryFile
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DictionaryObject,
    IndirectObject,
    StreamObject,
    TextStringObject,
)

PHOTO_COUNT = 5
PHOTO_BYTES = 5_000_000  # MB is decimal, not MiB; UI reports the same byte boundary.
ADOBE_METADATA_BYTES = 4096  # Technical bound on removable metadata, NOT on JCC.
PDF_SECURITY_ERROR = "PDF scripts, embedded files and active actions are not permitted."


@dataclass(frozen=True)
class ValidatedAttachment:
    stream: BinaryIO
    filename: str
    media_type: str
    byte_size: int
    pdf_page_count: int | None = None
    owns_stream: bool = False

    def close(self) -> None:
        """Release sanitized temporary output; uploaded streams belong to multipart."""
        if self.owns_stream:
            self.stream.close()


def safe_filename(value: str | None) -> str:
    # Original name is display metadata only, never a path or raw header value.
    name = (value or "attachment").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>"')
    return name[:255] or "attachment"


def stream_size(stream: BinaryIO) -> int:
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    return size


def _pdf_ending(stream: BinaryIO, size: int) -> bool:
    # Scan trailing whitespace in bounded blocks, not a whole-file rstrip().
    end = size
    while end:
        start = max(0, end - 65536)
        stream.seek(start)
        block = stream.read(end - start).rstrip()
        if block:
            end = start + len(block)
            stream.seek(max(0, end - 5))
            return stream.read(5) == b"%%EOF"
        end = start
    return False


def _pdf_reader(stream: BinaryIO, size: int) -> PdfReader:
    if not _pdf_ending(stream, size):
        raise ValueError("Malformed PDF document.")
    try:
        stream.seek(0)
        reader = PdfReader(stream, strict=True)
        if reader.is_encrypted:
            raise ValueError(
                "Encrypted PDF content cannot be validated. Use an unencrypted PDF."
            )
        page_count = len(reader.pages)
        if not page_count:
            raise ValueError("JCC PDF must contain at least one page.")
        return reader
    except ValueError:
        raise
    except Exception as exc:
        # Parser errors must not leak raw PDF content in responses/logs.
        raise ValueError("Malformed PDF document.") from exc


def _resolve(value):
    return value.get_object() if isinstance(value, IndirectObject) else value


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(PDF_SECURITY_ERROR)
        result[key] = value
    return result


def _adobe_metadata(association, seen_metadata):
    """Recognize only a single pageEntities FileSpec on an actual page /AF edge.

    No general embedded-file exception: this entire small structure is bounded
    and allowlisted; any other reference to its stream is still prohibited.
    """
    try:
        association = _resolve(association)
        if not isinstance(association, ArrayObject) or len(association) != 1:
            raise ValueError(PDF_SECURITY_ERROR)
        spec = _resolve(association[0])
        if (
            not isinstance(spec, DictionaryObject)
            or isinstance(spec, StreamObject)
            or set(spec)
            != {
                "/Type",
                "/F",
                "/UF",
                "/AFRelationship",
                "/EF",
            }
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        if (
            spec.get("/Type") != "/Filespec"
            or spec.get("/F") != "pageEntities.json"
            or spec.get("/UF") != "pageEntities.json"
            or spec.get("/AFRelationship") != "/ADBE_Contact_Private"
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        ef = _resolve(spec["/EF"])
        if (
            not isinstance(ef, DictionaryObject)
            or isinstance(ef, StreamObject)
            or set(ef) != {"/F"}
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        embedded = _resolve(ef["/F"])
        if (
            not isinstance(embedded, StreamObject)
            or embedded.get("/Type") != "/EmbeddedFile"
            or embedded.get("/Subtype") != "/application/json"
            or set(embedded)
            - {
                "/Type",
                "/Subtype",
                "/Length",
                "/Filter",
                "/DecodeParms",
                "/DL",
                "/Params",
            }
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        # Do not allow shared/duplicate payloads between page associations.
        for obj in (spec, embedded):
            if id(obj) in seen_metadata:
                raise ValueError(PDF_SECURITY_ERROR)
            seen_metadata.add(id(obj))
        params = _resolve(embedded.get("/Params", DictionaryObject()))
        if (
            not isinstance(params, DictionaryObject)
            or isinstance(params, StreamObject)
            or set(params) - {"/Size", "/CheckSum", "/CreationDate", "/ModDate"}
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        for key in ("/ModDate", "/CreationDate", "/CheckSum"):
            if key in params:
                value = _resolve(params[key])
                # Strings are metadata only: no date/code interpretation, no
                # nested dictionaries, actions or arbitrary unbounded values.
                if (
                    not isinstance(value, (TextStringObject, ByteStringObject))
                    or len(value) > 128
                ):
                    raise ValueError(PDF_SECURITY_ERROR)
        # pypdf stores encoded stream data in _data. Decode ONLY this recognized
        # tiny JSON with bounded zlib output, never unbounded get_data() or an
        # arbitrary PDF image/content stream. Other codecs/predictors are rejected.
        encoded = embedded._data
        if not isinstance(encoded, bytes) or len(encoded) > ADOBE_METADATA_BYTES * 2:
            raise ValueError(PDF_SECURITY_ERROR)
        codec = _resolve(embedded.get("/Filter"))
        if isinstance(codec, ArrayObject) and len(codec) == 1:
            codec = _resolve(codec[0])
        decode_params = _resolve(embedded.get("/DecodeParms"))
        if decode_params is not None and (
            not isinstance(decode_params, DictionaryObject)
            or isinstance(decode_params, StreamObject)
            or set(decode_params) - {"/Predictor"}
            or decode_params.get("/Predictor", 1) != 1
        ):
            raise ValueError(PDF_SECURITY_ERROR)
        if codec == "/FlateDecode":
            decoder = zlib.decompressobj()
            payload = decoder.decompress(encoded, ADOBE_METADATA_BYTES + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError(PDF_SECURITY_ERROR)
        elif codec is None:
            payload = encoded
        else:
            raise ValueError(PDF_SECURITY_ERROR)
        if len(payload) > ADOBE_METADATA_BYTES:
            raise ValueError(PDF_SECURITY_ERROR)
        for container, key in ((embedded, "/DL"), (params, "/Size")):
            if key in container:
                length = _resolve(container[key])
                if (
                    not isinstance(length, int)
                    or isinstance(length, bool)
                    or length < 0
                    or length != len(payload)
                ):
                    raise ValueError(PDF_SECURITY_ERROR)
        metadata = json.loads(payload.decode("utf-8"), object_pairs_hook=_json_object)
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"type", "isBackSide"}
            or not isinstance(metadata["type"], str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,63}", metadata["type"]) is None
            or type(metadata["isBackSide"]) is not bool
        ):
            raise ValueError(PDF_SECURITY_ERROR)
    except Exception as exc:
        raise ValueError(PDF_SECURITY_ERROR) from exc


def _pdf_security(reader: PdfReader, *, allow_adobe=False):
    page_objects = {
        id(_resolve(page.indirect_reference)) if page.indirect_reference else id(page)
        for page in reader.pages
    }
    seen, seen_metadata, adobe_pages = set(), set(), []
    pending = [reader.trailer]
    forbidden = {
        "/JS",
        "/JavaScript",
        "/AA",
        "/OpenAction",
        "/Launch",
        "/EmbeddedFiles",
        "/EmbeddedFile",
        "/RichMedia",
        "/XFA",
    }
    while pending:
        item = pending.pop()
        if isinstance(item, IndirectObject):
            identity = (item.generation, item.idnum)
            if identity in seen:
                continue
            seen.add(identity)
            item = item.get_object()
            if item is None:
                raise ValueError("PDF contains an invalid object reference.")
        if isinstance(item, DictionaryObject):
            if (
                forbidden.intersection(item)
                or item.get("/S")
                in ("/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR")
                or item.get("/Type") == "/EmbeddedFile"
            ):
                raise ValueError(PDF_SECURITY_ERROR)
            for key, value in item.items():
                if allow_adobe and key == "/AF" and id(item) in page_objects:
                    # Exempt only this approved page edge, never all references
                    # to the same object: aliases elsewhere must still fail.
                    _adobe_metadata(value, seen_metadata)
                    adobe_pages.append(item)
                else:
                    pending.append(value)
        elif isinstance(item, ArrayObject):
            pending.extend(item)
    return adobe_pages


def _pdf(stream: BinaryIO, size: int) -> int:
    """Original strict security policy, with NO removable metadata exception."""
    reader = _pdf_reader(stream, size)
    _pdf_security(reader)
    return len(reader.pages)


def _validated_pdf(stream: BinaryIO, size: int, filename: str | None):
    try:
        reader = _pdf_reader(stream, size)
        adobe_pages = _pdf_security(reader, allow_adobe=True)
        pages = len(reader.pages)
        if not adobe_pages:
            stream.seek(0)
            return ValidatedAttachment(
                stream, safe_filename(filename), "application/pdf", size, pages
            )
        # Remove only preflight-approved edges BEFORE cloning so their unreferenced
        # FileSpec/EmbeddedFile objects are never copied into the sanitized file.
        approved_page_ids = {id(page) for page in adobe_pages}
        for page in adobe_pages:
            del page["/AF"]
        for page in reader.pages:
            if id(_resolve(page.indirect_reference)) in approved_page_ids:
                page.pop("/AF", None)
        # The caller owns this stream after validation; close it on every failure.
        output = TemporaryFile(mode="w+b")  # noqa: SIM115
        try:
            PdfWriter(clone_from=reader).write(output)
            sanitized_size = stream_size(output)
            if _pdf(output, sanitized_size) != pages:
                raise ValueError("Malformed PDF document.")
            output.seek(0)
            return ValidatedAttachment(
                output,
                safe_filename(filename),
                "application/pdf",
                sanitized_size,
                pages,
                owns_stream=True,
            )
        except BaseException:
            output.close()
            raise
    except (ValueError, OSError):
        raise
    except Exception as exc:
        raise ValueError("Malformed PDF document.") from exc


def validate_stream(
    stream: BinaryIO, filename: str | None, *, allow_pdf: bool
) -> ValidatedAttachment:
    size = stream_size(stream)
    if not size:
        raise ValueError("Select a non-empty attachment.")
    prefix = stream.read(5)
    stream.seek(0)
    if prefix == b"%PDF-":
        if not allow_pdf:
            raise ValueError("Tower Photos must be JPG/JPEG or PNG, not PDF.")
        return _validated_pdf(stream, size, filename)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(stream, formats=["JPEG", "PNG"]) as image:
                fmt = image.format
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Use a single-page JPG/JPEG or PNG image.")
                image.verify()
            stream.seek(0)
            with Image.open(stream, formats=["JPEG", "PNG"]) as image:
                image.load()  # Full decode rejects truncated/malformed pixel data.
        end = b"\x00\x00\x00\x00IEND\xaeB`\x82" if fmt == "PNG" else b"\xff\xd9"
        stream.seek(max(0, size - len(end)))
        if stream.read(len(end)) != end:
            raise ValueError("Malformed image or trailing non-image payload.")
        stream.seek(0)
        return ValidatedAttachment(
            stream,
            safe_filename(filename),
            "image/png" if fmt == "PNG" else "image/jpeg",
            size,
        )
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError(
            "Use a valid JPG/JPEG, PNG" + (" or PDF." if allow_pdf else ".")
        ) from exc


def validate_content(
    raw: bytes, filename: str | None, *, allow_pdf: bool
) -> ValidatedAttachment:
    """Convenience for already bounded in-memory fixtures; uploads use streams."""
    return validate_stream(BytesIO(raw), filename, allow_pdf=allow_pdf)
