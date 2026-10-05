"""Bounded file IO and disk-backed multipart regression checks."""

import zlib
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import EncodedStreamObject, NameObject
from starlette.datastructures import UploadFile
from starlette.responses import StreamingResponse

from app.ecr import attachment_routes, attachments
from app.ecr.attachment_validation import PHOTO_BYTES, validate_stream
from app.storage.attachments import CHUNK_SIZE, LocalAttachmentStorage, iter_chunks
from tests import test_attachments as existing
from tests.test_attachments import image_bytes, pdf_bytes, records, send

evidence = existing.evidence


class BoundedReader(BytesIO):
    def read(self, size=-1):
        assert size >= 0, "Unbounded file read"
        return super().read(size)


@pytest.mark.parametrize("content", [pdf_bytes(3), image_bytes(), image_bytes("JPEG")])
def test_seekable_validation_never_reads_whole_file(content):
    source = BoundedReader(content)
    validated = validate_stream(source, "page", allow_pdf=True)
    assert validated.stream is source
    assert validated.byte_size == len(content)
    assert source.tell() == 0
    assert not hasattr(validated, "content")


def test_stream_store_and_reader_use_bounded_chunks(tmp_path):
    store = LocalAttachmentStorage(tmp_path / "private")
    raw = b"x" * (CHUNK_SIZE * 4 + 1)
    key = store.put_stream(BoundedReader(raw), "application/pdf")
    reader = store.open_reader(key)
    chunks = list(iter_chunks(reader))
    assert len(chunks) == 5
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert b"".join(chunks) == raw
    assert reader.closed


def test_pdf_security_walk_does_not_decompress_arbitrary_streams(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Object graph walk must not decompress arbitrary streams")

    monkeypatch.setattr(EncodedStreamObject, "get_data", forbidden)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = EncodedStreamObject()
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream._data = zlib.compress(b"q Q\n" * 100_000)
    writer._root_object[NameObject("/Extra")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    assert (
        validate_stream(
            BoundedReader(out.getvalue()), "jcc.pdf", allow_pdf=True
        ).pdf_page_count
        == 1
    )


def test_large_jcc_and_multiple_pages_stage_disk_streams(
    client, db_session, evidence, monkeypatch
):
    _, report, token, store = evidence
    original = store.put_stream
    seen = []

    def stream_only(source, media_type):
        # All uploaded pages, even tiny ones, live on disk before decoding.
        assert source._rolled
        assert source.tell() == 0
        seen.append(media_type)
        return original(source, media_type)

    async def no_upload_read(*args, **kwargs):
        pytest.fail("HTTP uploads must use seekable files, not UploadFile.read")

    monkeypatch.setattr(UploadFile, "read", no_upload_read)
    monkeypatch.setattr(store, "put_stream", stream_only)
    large = image_bytes(exact_size=PHOTO_BYTES + 1)
    result = send(
        client,
        report,
        token,
        files=[
            ("large.png", large),
            ("second.jpg", image_bytes("JPEG")),
            ("third.png", image_bytes()),
        ],
    )
    assert result.status_code == 200
    rows = records(db_session, report)
    assert len(rows) == 3 and rows[0].byte_size == PHOTO_BYTES + 1
    assert seen == ["image/png", "image/jpeg", "image/png"]
    assert all(r.kind == "JCC" for r in rows)


def test_private_download_constructs_streaming_response(
    client, db_session, evidence, monkeypatch
):
    _, report, token, store = evidence
    raw = pdf_bytes(3)
    assert send(client, report, token, files=[("jcc.pdf", raw)]).status_code == 200
    row = records(db_session, report)[0]

    def no_whole_read(*args):
        pytest.fail("Private route must not call storage.read")

    seen = []
    original_response = attachment_routes.StreamingResponse

    def response(content, **kwargs):
        assert not isinstance(content, bytes)
        result = original_response(content, **kwargs)
        assert isinstance(result, StreamingResponse)
        seen.append(result)
        return result

    monkeypatch.setattr(store, "read", no_whole_read)
    monkeypatch.setattr(attachment_routes, "StreamingResponse", response)
    url = f"/ecr/packages/{report.tower.package_id}/attachments/{row.id}"
    result = client.get(url)
    assert len(seen) == 1
    assert result.content == raw
    assert result.headers["content-length"] == str(len(raw))
    assert result.headers["cache-control"] == "private, no-store"


def test_failed_validation_removes_previously_staged_pages(
    client, db_session, evidence
):
    _, report, token, store = evidence
    result = send(
        client,
        report,
        token,
        files=[("good.png", image_bytes()), ("bad.pdf", b"%PDF-malformed")],
    )
    assert result.status_code == 422
    assert not records(db_session, report)
    assert not list(store.root.iterdir())


def test_unauthorized_upload_never_stages_files(client, evidence, monkeypatch):
    _, report, token, store = evidence
    client.cookies.clear()

    def forbidden(*args):
        pytest.fail("Unauthorized users must not stage protected objects")

    monkeypatch.setattr(attachments, "stage", forbidden)
    assert (
        send(client, report, token, files=[("jcc.pdf", pdf_bytes())]).status_code == 303
    )
    assert not store.root.exists()
