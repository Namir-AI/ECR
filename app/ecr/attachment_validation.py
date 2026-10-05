"""Content-based evidence validation, independent of filenames/browser MIME."""

import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

PHOTO_COUNT = 5
PHOTO_BYTES = 5_000_000  # MB is decimal, not MiB; UI reports the same byte boundary.


@dataclass(frozen=True)
class ValidatedAttachment:
    stream: BinaryIO
    filename: str
    media_type: str
    byte_size: int
    pdf_page_count: int | None = None


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


def _pdf(stream: BinaryIO, size: int) -> int:
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
        seen = set()
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
                    in (
                        "/JavaScript",
                        "/Launch",
                        "/SubmitForm",
                        "/ImportData",
                        "/GoToR",
                    )
                    or item.get("/Type") == "/EmbeddedFile"
                ):
                    raise ValueError(
                        "PDF scripts, embedded files and active actions are not permitted."
                    )
                pending.extend(item.values())
            elif isinstance(item, ArrayObject):
                pending.extend(item)
        # Active actions live in dictionaries/references, not drawing operators.
        # Do not decompress arbitrary image/content streams just to inspect them.
        return page_count
    except ValueError:
        raise
    except Exception as exc:
        # Parser errors must not leak raw PDF content in responses/logs.
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
        pages = _pdf(stream, size)
        stream.seek(0)
        return ValidatedAttachment(
            stream, safe_filename(filename), "application/pdf", size, pages
        )
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
