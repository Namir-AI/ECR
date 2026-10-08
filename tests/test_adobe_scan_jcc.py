"""Synthetic Adobe page metadata: remove narrowly, then apply strict PDF policy."""

import hashlib
import json
import zlib
from io import BytesIO
from tempfile import TemporaryFile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

from app.ecr import attachment_validation as validation
from app.ecr import attachments
from app.storage.attachments import LocalAttachmentStorage
from tests import test_attachments as existing
from tests.test_attachment_streaming import BoundedReader
from tests.test_attachments import pdf_bytes, records, send

evidence = existing.evidence
PIXELS = b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff"


def adobe_pdf(*, pages=1, payload=None, compressed=True, edit=None):
    """Real page/AF/FileSpec/EmbeddedFile graph; never a customer document."""
    writer = PdfWriter()
    specs, embedded = [], []
    for index in range(pages):
        page = writer.add_blank_page(width=240 + index, height=320 + index)
        page.rotate(90 if index % 2 else 180)
        image = EncodedStreamObject()
        image.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(2),
                NameObject("/Height"): NumberObject(2),
                NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/Filter"): NameObject("/FlateDecode"),
            }
        )
        image._data = zlib.compress(PIXELS)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/XObject"): DictionaryObject(
                    {NameObject("/Im0"): writer._add_object(image)}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(b"q 200 0 0 250 10 10 cm /Im0 Do Q\n")
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Annots")] = ArrayObject(
            [
                writer._add_object(
                    DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Annot"),
                            NameObject("/Subtype"): NameObject("/Text"),
                            NameObject("/Rect"): ArrayObject(
                                [NumberObject(n) for n in (5, 5, 15, 15)]
                            ),
                            NameObject("/Contents"): TextStringObject(
                                f"Visible note {index + 1}"
                            ),
                        }
                    )
                )
            ]
        )
        data = (
            payload
            if payload is not None
            else b'{ "type": "'
            + (b"Document" if index == 0 else b"Form")
            + b'", "isBackSide": false }'
        )
        stream = EncodedStreamObject() if compressed else DecodedStreamObject()
        stream[NameObject("/Type")] = NameObject("/EmbeddedFile")
        stream[NameObject("/Subtype")] = NameObject("/application/json")
        stream[NameObject("/DL")] = NumberObject(len(data))
        stream[NameObject("/Params")] = DictionaryObject(
            {
                NameObject("/Size"): NumberObject(len(data)),
                NameObject("/ModDate"): TextStringObject("D:20260814111025+05'30'"),
            }
        )
        if compressed:
            stream[NameObject("/Filter")] = NameObject("/FlateDecode")
            stream._data = zlib.compress(data)
        else:
            stream.set_data(data)
        spec = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Filespec"),
                NameObject("/F"): TextStringObject("pageEntities.json"),
                NameObject("/UF"): TextStringObject("pageEntities.json"),
                NameObject("/AFRelationship"): NameObject("/ADBE_Contact_Private"),
                NameObject("/EF"): DictionaryObject(
                    {NameObject("/F"): writer._add_object(stream)}
                ),
            }
        )
        page[NameObject("/AF")] = ArrayObject([writer._add_object(spec)])
        specs.append(spec)
        embedded.append(stream)
    writer.add_metadata({"/Title": "Retain ordinary document metadata"})
    if edit:
        edit(writer, specs, embedded)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def assert_sanitized(raw, pages):
    assert b"pageEntities.json" not in raw
    assert b"/EmbeddedFile" not in raw  # also excludes /EmbeddedFiles
    reader = PdfReader(BytesIO(raw), strict=True)
    assert len(reader.pages) == pages
    assert all("/AF" not in page for page in reader.pages)
    assert validation._pdf(BoundedReader(raw), len(raw)) == pages


def test_exact_real_adobe_structure_strict_rejects_then_preflight_and_sanitizer_accept():
    raw = adobe_pdf(pages=3)
    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation._pdf(BytesIO(raw), len(raw))
    reader = PdfReader(BytesIO(raw), strict=True)
    for page, expected_length, expected_type in zip(
        reader.pages, (43, 39, 39), ("Document", "Form", "Form"), strict=True
    ):
        association = page["/AF"]
        assert len(association) == 1
        spec = association[0].get_object()
        assert spec["/F"] == spec["/UF"] == "pageEntities.json"
        assert spec["/AFRelationship"] == "/ADBE_Contact_Private"
        stream = spec["/EF"]["/F"]
        assert stream["/Filter"] == "/FlateDecode"
        assert stream["/DL"] == stream["/Params"]["/Size"] == expected_length
        assert stream["/Params"]["/ModDate"] == "D:20260814111025+05'30'"
        assert len(stream.get_data()) == expected_length
        assert json.loads(stream.get_data()) == {
            "type": expected_type,
            "isBackSide": False,
        }
        # pypdf resolves the same indirect objects through both traversals.
        assert page.indirect_reference.get_object()["/AF"][0].get_object() is spec
    assert len(validation._pdf_security(reader, allow_adobe=True)) == 3
    result = validation.validate_stream(
        BoundedReader(raw), "Adobe JCC.pdf", allow_pdf=True
    )
    try:
        assert result.pdf_page_count == 3
        assert_sanitized(result.stream.read(result.byte_size), 3)
    finally:
        result.close()


@pytest.mark.parametrize(
    "field", ["baseline", "Filter", "DL", "Params", "Size", "ModDate", "all"]
)
def test_observed_standard_stream_fields_pass_in_isolation(field):
    def edit(_, __, streams):
        for stream in streams:
            params = stream.pop("/Params")
            if field not in ("DL", "all"):
                stream.pop("/DL")
            if field in ("Params", "Size", "ModDate", "all"):
                if field not in ("Size", "all"):
                    params.pop("/Size")
                if field not in ("ModDate", "all"):
                    params.pop("/ModDate")
                stream[NameObject("/Params")] = params

    result = validation.validate_content(
        adobe_pdf(pages=3, edit=edit), "jcc.pdf", allow_pdf=True
    )
    result.close()


@pytest.mark.parametrize("field", ["DL", "Params", "Size", "ModDate"])
def test_observed_standard_metadata_fields_are_optional(field):
    def edit(_, __, streams):
        for stream in streams:
            if field in ("DL", "Params"):
                stream.pop("/" + field)
            else:
                stream["/Params"].pop("/" + field)

    result = validation.validate_content(
        adobe_pdf(pages=3, edit=edit), "jcc.pdf", allow_pdf=True
    )
    result.close()


@pytest.mark.parametrize("field", ["/DL", "/Size"])
@pytest.mark.parametrize(
    "value",
    [
        NumberObject(-1),
        NumberObject(0),
        NumberObject(42),
        NumberObject(4097),
        FloatObject(43.5),
        TextStringObject("43"),
        BooleanObject(True),
        NullObject(),
        ArrayObject([NumberObject(43)]),
        DictionaryObject({NameObject("/S"): NameObject("/Launch")}),
    ],
)
def test_declared_decoded_lengths_must_be_nonnegative_integers_matching_payload(
    field, value
):
    def edit(_, __, streams):
        container = streams[0] if field == "/DL" else streams[0]["/Params"]
        container[NameObject(field)] = value

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize(
    "value",
    [
        TextStringObject("x" * 129),
        NumberObject(43),
        NameObject("/JavaScript"),
        ArrayObject([TextStringObject("D:20260814111025")]),
        DictionaryObject({NameObject("/S"): NameObject("/Launch")}),
        DecodedStreamObject(),
        BooleanObject(False),
        NullObject(),
    ],
)
def test_moddate_is_only_a_bounded_pdf_string(value):
    def edit(_, __, streams):
        streams[0]["/Params"][NameObject("/ModDate")] = value

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize("filter_array", [False, True])
def test_indirect_standard_metadata_resolution_with_normal_flate_filter(filter_array):
    def edit(writer, _, streams):
        for stream in streams:
            if filter_array:
                stream[NameObject("/Filter")] = ArrayObject(
                    [NameObject("/FlateDecode")]
                )
            params = stream["/Params"]
            stream[NameObject("/DL")] = writer._add_object(stream["/DL"])
            for key in ("/Size", "/ModDate"):
                params[NameObject(key)] = writer._add_object(params[key])
            stream[NameObject("/Params")] = writer._add_object(params)

    result = validation.validate_content(
        adobe_pdf(pages=3, edit=edit), "jcc.pdf", allow_pdf=True
    )
    try:
        assert_sanitized(result.stream.read(result.byte_size), 3)
    finally:
        result.close()


@pytest.mark.parametrize("pages", [1, 3])
@pytest.mark.parametrize("kind", ["Document", "Form"])
@pytest.mark.parametrize("compressed", [False, True])
def test_adobe_accepted_rewritten_and_content_preserved(pages, kind, compressed):
    raw = adobe_pdf(
        pages=pages,
        payload=json.dumps({"type": kind, "isBackSide": False}).encode(),
        compressed=compressed,
    )
    source = BoundedReader(raw)
    result = validation.validate_stream(
        source, "../../Adobe Scan JCC.pdf", allow_pdf=True
    )
    try:
        assert result.owns_stream and result.stream is not source
        assert result.stream.fileno() >= 0  # disk-backed, not complete PDF bytes
        assert result.filename == "Adobe Scan JCC.pdf"
        assert result.media_type == "application/pdf" and result.pdf_page_count == pages
        assert result.byte_size == validation.stream_size(result.stream)
        sanitized = result.stream.read(result.byte_size)
        assert sanitized != raw
        assert_sanitized(sanitized, pages)
        before, after = PdfReader(BytesIO(raw)), PdfReader(BytesIO(sanitized))
        assert after.metadata.title == before.metadata.title
        for old, new in zip(before.pages, after.pages, strict=True):
            assert list(old.mediabox) == list(new.mediabox)
            assert old.rotation == new.rotation
            assert old["/Contents"].get_data() == new["/Contents"].get_data()
            assert (
                old["/Annots"][0].get_object()["/Contents"]
                == new["/Annots"][0].get_object()["/Contents"]
            )
            old_image = old["/Resources"]["/XObject"]["/Im0"]
            new_image = new["/Resources"]["/XObject"]["/Im0"]
            assert old_image._data == new_image._data  # no recompression
            assert (
                hashlib.sha256(old_image.get_data()).digest()
                == hashlib.sha256(new_image.get_data()).digest()
            )
    finally:
        result.close()
    assert result.stream.closed and not source.closed
    assert source.getvalue() == raw


def test_preflight_followed_by_exception_free_strict_validation(monkeypatch):
    calls = []
    original = validation._pdf_security

    def checked(reader, *, allow_adobe=False):
        calls.append(allow_adobe)
        return original(reader, allow_adobe=allow_adobe)

    monkeypatch.setattr(validation, "_pdf_security", checked)
    result = validation.validate_content(adobe_pdf(), "jcc.pdf", allow_pdf=True)
    result.close()
    assert calls == [True, False]


def test_no_arbitrary_stream_decompression_and_bounded_upload_reads(monkeypatch):
    raw = adobe_pdf(pages=3)

    def forbidden(*args, **kwargs):
        pytest.fail("Security/cloning must not decode scanned image/content streams")

    monkeypatch.setattr(EncodedStreamObject, "get_data", forbidden)
    result = validation.validate_stream(BoundedReader(raw), "jcc.pdf", allow_pdf=True)
    result.close()


@pytest.mark.parametrize(
    "payload",
    [
        b"not JSON",
        b"\xff",
        b"[]",
        b"null",
        b'{"type":{"nested":"Document"},"isBackSide":false}',
        b'{"type":"Document","isBackSide":[]}',
        b'{"type":"Document","isBackSide":0}',
        b'{"type":"Document","isBackSide":false,"extra":"payload"}',
        b'{"type":"Document","type":"Form","isBackSide":false}',
        b'{"type":"Document","isBackSide":false,"isBackSide":true}',
        b'{"type":"Document"}',
        b'{"isBackSide":false}',
        b'{"type":"<script>evil()</script>","isBackSide":false}',
        b'{"type":"' + b"A" * 65 + b'","isBackSide":false}',
        b'{"type":"Document","isBackSide":false}' + b" " * 4096,
        b"x" * 1_000_000,  # tiny compressed input, oversized decoded metadata
    ],
)
def test_unrecognized_metadata_payload_is_rejected(payload):
    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(
            adobe_pdf(payload=payload), "jcc.pdf", allow_pdf=True
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("/F", "attachment.exe"),
        ("/UF", "another.json"),
        ("/AFRelationship", "/Data"),
        ("/Type", "/Unknown"),
    ],
)
def test_unrecognized_filespec_is_rejected(field, value):
    def edit(_, specs, __):
        specs[0][NameObject(field)] = TextStringObject(value)

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("/Subtype", "/text/plain"),
        ("/Type", "/Unknown"),
        ("/Filter", "/LZWDecode"),
        ("/AA", "/Unsafe"),
    ],
)
def test_unrecognized_metadata_stream_is_rejected(field, value):
    def edit(_, __, streams):
        streams[0][NameObject(field)] = NameObject(value)

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize(
    "feature",
    [
        "/JS",
        "/JavaScript",
        "/AA",
        "/OpenAction",
        "/Launch",
        "/XFA",
        "/RichMedia",
    ],
)
def test_valid_adobe_plus_active_feature_rejected_before_sanitization(
    feature, monkeypatch
):
    def edit(writer, _, __):
        writer._root_object[NameObject(feature)] = TextStringObject("unsafe")

    def no_output(*args, **kwargs):
        pytest.fail("Mixed unsafe document must not reach sanitization")

    monkeypatch.setattr(validation, "TemporaryFile", no_output)
    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize(
    "action", ["/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR"]
)
def test_valid_adobe_plus_prohibited_action_rejected(action):
    def edit(writer, _, __):
        writer._root_object[NameObject("/OtherAction")] = DictionaryObject(
            {NameObject("/S"): NameObject(action)}
        )

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(adobe_pdf(edit=edit), "jcc.pdf", allow_pdf=True)


@pytest.mark.parametrize(
    "location",
    [
        "document_af",
        "names",
        "unknown_second",
        "duplicate",
        "stream_alias",
        "spec_alias",
        "shared_pages",
        "extra_ef",
        "impostor_page",
    ],
)
def test_metadata_cannot_be_aliased_or_moved_outside_single_page_association(location):
    def edit(writer, specs, streams):
        root, page = writer._root_object, writer.pages[0]
        if location == "document_af":
            root[NameObject("/AF")] = page.pop("/AF")
        elif location == "names":
            root[NameObject("/Names")] = DictionaryObject(
                {NameObject("/EmbeddedFiles"): DictionaryObject()}
            )
        elif location == "unknown_second":
            root[NameObject("/AnotherFile")] = writer._add_object(DecodedStreamObject())
            root["/AnotherFile"][NameObject("/Type")] = NameObject("/EmbeddedFile")
        elif location == "duplicate":
            page["/AF"].append(page["/AF"][0])
        elif location == "stream_alias":
            root[NameObject("/AnotherReference")] = specs[0]["/EF"].raw_get("/F")
        elif location == "spec_alias":
            root[NameObject("/AnotherReference")] = page["/AF"][0]
        elif location == "shared_pages":
            writer.pages[1][NameObject("/AF")] = page["/AF"]
        elif location == "extra_ef":
            specs[0]["/EF"][NameObject("/UF")] = writer._add_object(streams[0])
        elif location == "impostor_page":
            root[NameObject("/Impostor")] = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Page"),
                    NameObject("/AF"): page.pop("/AF"),
                }
            )

    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation.validate_content(
            adobe_pdf(pages=2, edit=edit), "jcc.pdf", allow_pdf=True
        )


def test_metadata_payload_exact_technical_boundary():
    payload = b'{"type":"Document","isBackSide":false}'
    payload += b" " * (validation.ADOBE_METADATA_BYTES - len(payload))
    result = validation.validate_content(
        adobe_pdf(payload=payload), "jcc.pdf", allow_pdf=True
    )
    result.close()


def test_strict_mode_still_rejects_unsanitized_adobe():
    raw = adobe_pdf()
    with pytest.raises(ValueError, match="scripts, embedded files and active actions"):
        validation._pdf(BytesIO(raw), len(raw))


def test_regular_pdf_not_rewritten_or_owned():
    raw = pdf_bytes(3)
    source = BoundedReader(raw)
    result = validation.validate_stream(source, "ordinary.pdf", allow_pdf=True)
    assert result.stream is source and not result.owns_stream
    result.close()
    assert not source.closed and source.getvalue() == raw


def test_route_stores_only_sanitized_pdf_and_private_downloads_it(
    client, db_session, evidence, monkeypatch
):
    _, report, token, store = evidence
    raw = adobe_pdf(pages=3)
    staged_streams = []
    original = store.put_stream

    def sanitized_only(source, media_type):
        assert not isinstance(source, BytesIO) and source.tell() == 0
        assert source.fileno() >= 0
        strict_pages = validation._pdf(source, validation.stream_size(source))
        assert strict_pages == 3
        source.seek(0)
        staged_streams.append(source)
        return original(source, media_type)

    monkeypatch.setattr(store, "put_stream", sanitized_only)
    response = send(client, report, token, files=[("../../Adobe Scan JCC.pdf", raw)])
    assert response.status_code == 200, response.text
    row = records(db_session, report)[0]
    stored = store.read(row.storage_key)
    assert stored != raw
    assert row.display_filename == "Adobe Scan JCC.pdf" and row.pdf_page_count == 3
    assert row.media_type == "application/pdf" and row.byte_size == len(stored)
    assert row.byte_size != len(raw)
    assert_sanitized(stored, 3)
    assert all(stream.closed for stream in staged_streams)
    for query in ("", "?download=true"):
        response = client.get(
            f"/ecr/packages/{row.package_id}/attachments/{row.id}{query}"
        )
        assert response.status_code == 200 and response.content == stored
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-length"] == str(len(stored))
        assert response.headers["cache-control"] == "private, no-store"
        assert_sanitized(response.content, 3)


@pytest.mark.parametrize("replacing", [False, True])
def test_second_strict_validation_failure_publishes_nothing_preserves_prior_and_closes_output(
    client, db_session, evidence, monkeypatch, replacing
):
    _, report, token, store = evidence
    if replacing:
        assert (
            send(client, report, token, files=[("old.pdf", pdf_bytes())]).status_code
            == 200
        )
    before = [(r.id, r.storage_key) for r in records(db_session, report)]
    outputs = []

    def temporary(*args, **kwargs):
        stream = TemporaryFile(*args, **kwargs)  # noqa: SIM115 -- returned to validator
        outputs.append(stream)
        return stream

    def fail_second(*args):
        raise ValueError(validation.PDF_SECURITY_ERROR)

    monkeypatch.setattr(validation, "TemporaryFile", temporary)
    monkeypatch.setattr(validation, "_pdf", fail_second)
    response = send(
        client,
        report,
        token,
        "JCC_REPLACE" if replacing else "JCC_ADD",
        files=[("new.pdf", adobe_pdf())],
    )
    assert response.status_code == 422
    assert [(r.id, r.storage_key) for r in records(db_session, report)] == before
    objects = [p.name for p in store.root.iterdir()] if store.root.exists() else []
    assert objects == [key for _, key in before]
    if replacing:
        assert store.read(before[0][1]) == pdf_bytes()
    assert len(outputs) == 1 and outputs[0].closed


@pytest.mark.parametrize("fail", [False, True])
def test_sanitized_stream_is_closed_after_successful_or_failed_staging(
    tmp_path, monkeypatch, fail
):
    source = BoundedReader(adobe_pdf())
    validated = validation.validate_stream(source, "jcc.pdf", allow_pdf=True)
    store = LocalAttachmentStorage(tmp_path / "private")
    if fail:

        def broken(*args):
            raise OSError("staging unavailable")

        monkeypatch.setattr(store, "put_stream", broken)
        with pytest.raises(OSError):
            attachments.stage(store, validated)
        assert not store.root.exists()
    else:
        staged = attachments.stage(store, validated)
        assert_sanitized(store.read(staged.storage_key), 1)
    assert validated.stream.closed and not source.closed


def test_rewrite_failure_closes_private_temporary_output(monkeypatch):
    outputs = []

    def temporary(*args, **kwargs):
        stream = TemporaryFile(*args, **kwargs)  # noqa: SIM115 -- returned to validator
        outputs.append(stream)
        return stream

    raw = adobe_pdf()

    def failure(*args, **kwargs):
        raise OSError("temporary output failure")

    monkeypatch.setattr(validation, "TemporaryFile", temporary)
    monkeypatch.setattr(validation.PdfWriter, "write", failure)
    with pytest.raises(OSError):
        validation.validate_content(raw, "jcc.pdf", allow_pdf=True)
    assert len(outputs) == 1 and outputs[0].closed
