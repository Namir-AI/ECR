"""Package evidence: real MySQL, private files, content validation and failures."""

import json
import re
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.templates import file_size
from app.ecr import attachments
from app.ecr.attachment_models import (
    EcrJccDocument,
    EcrPackageAuditEvent,
)
from app.ecr.attachment_validation import PHOTO_BYTES, validate_content
from app.ecr.models import EcrReport, EcrReportStatus, EcrTower
from app.ecr.workflow import final_errors
from app.storage.attachments import LocalAttachmentStorage
from app.users.models import UserRole
from tests.conftest import csrf_from, login
from tests.test_phase3 import _create_report


def image_bytes(fmt="PNG", *, exact_size=None):
    out = BytesIO()
    Image.new("RGB", (12, 10), "orange").save(out, format=fmt)
    if exact_size is not None:
        # Valid PNG text chunk padding, not unvalidated trailing garbage.
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("padding", "x" * (exact_size - len(out.getvalue()) - 20))
        out = BytesIO()
        Image.new("RGB", (12, 10), "orange").save(out, format="PNG", pnginfo=metadata)
        assert len(out.getvalue()) == exact_size
    return out.getvalue()


def pdf_bytes(pages=1, *, script=False, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if script:
        writer.add_open_action(
            DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("app.alert('unsafe');"),
                }
            )
        )
    if encrypted:
        writer.encrypt("private")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def evidence(client, db_session, user_factory, tmp_path):
    owner = user_factory()
    report = _create_report(db_session, owner)
    store = LocalAttachmentStorage(tmp_path / "protected")
    client.app.state.attachment_storage = store
    login(client, owner.employee_id)
    package_id = report.tower.package_id
    token = csrf_from(client.get(f"/ecr/packages/{package_id}/attachments").text)
    return owner, report, token, store


def send(client, report, token, action="JCC_ADD", files=None, **data):
    upload = [
        ("files", (name, content, "application/octet-stream"))
        for name, content in files or []
    ]
    return client.post(
        f"/ecr/packages/{report.tower.package_id}/attachments",
        data={"action": action, **data},
        files=upload or None,
        headers={"X-CSRF-Token": token},
    )


def records(db, report):
    return attachments.file_records(db, report.tower.package_id)


def extra_cell(db, report, owner, *, status=EcrReportStatus.DRAFT, another_tower=False):
    tower = report.tower
    if another_tower:
        # Package is legitimately multi-tower; preserve all existing identities.
        report.tower.package.multiple_towers = True
        report.tower.tower_suffix = "A"
        report.tower.normalized_suffix_key = "A"
        tower = EcrTower(
            package_id=report.tower.package_id,
            tower_suffix="B",
            normalized_suffix_key="B",
        )
        db.add(tower)
        db.flush()
    row = EcrReport(
        tower_id=tower.id,
        cell_no=1 if another_tower else 2,
        supervisor_user_id=owner.id,
        branch_id=owner.branch_id,
        status=status,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("pages", [1, 3])
def test_single_and_multipage_pdf(client, db_session, evidence, pages):
    _, report, token, store = evidence
    raw = pdf_bytes(pages)
    result = send(client, report, token, files=[("../../jcc.pdf", raw)])
    assert result.status_code == 200, result.text
    row = records(db_session, report)[0]
    assert row.kind == "JCC" and row.media_type == "application/pdf"
    assert row.pdf_page_count == pages and row.display_filename == "jcc.pdf"
    assert store.read(row.storage_key) == raw
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", row.storage_key)
    content = client.get(f"/ecr/packages/{row.package_id}/attachments/{row.id}")
    assert (
        content.content == raw and content.headers["content-type"] == "application/pdf"
    )
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in content.headers["content-security-policy"]
    assert (
        row.storage_key
        not in client.get(f"/ecr/packages/{row.package_id}/attachments").text
    )


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_jcc_images_order_add_reorder_remove_not_photo_pool(
    client, db_session, evidence, fmt
):
    _, report, token, store = evidence
    assert (
        send(
            client,
            report,
            token,
            files=[(f"page{i}.wrong", image_bytes(fmt)) for i in range(3)],
        ).status_code
        == 200
    )
    rows = records(db_session, report)
    logical = db_session.scalar(
        select(EcrJccDocument).where(
            EcrJccDocument.package_id == report.tower.package_id
        )
    )
    assert logical.format == "IMAGES" and {r.document_id for r in rows} == {logical.id}
    assert [r.position for r in rows] == [1, 2, 3]
    ids = [r.id for r in rows][::-1]
    assert (
        send(client, report, token, "JCC_REORDER", order=json.dumps(ids)).status_code
        == 200
    )
    assert [r.id for r in records(db_session, report)] == ids
    assert (
        send(
            client, report, token, "JCC_ADD", files=[("extra.png", image_bytes())]
        ).status_code
        == 200
    )
    assert len(records(db_session, report)) == 4
    assert (
        "0 of 5"
        in client.get(f"/ecr/packages/{report.tower.package_id}/attachments").text
    )
    removed = records(db_session, report)[1]
    old_key = removed.storage_key
    assert (
        send(client, report, token, "JCC_REMOVE", target_id=removed.id).status_code
        == 200
    )
    with pytest.raises(FileNotFoundError):
        store.read(old_key)
    assert [r.position for r in records(db_session, report)] == [1, 2, 3]
    assert send(client, report, token, "JCC_REMOVE").status_code == 200
    assert records(db_session, report) == []
    assert (
        db_session.scalar(
            select(EcrJccDocument).where(
                EcrJccDocument.package_id == report.tower.package_id
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "content",
    [
        b"%PDF-1.7\nnot a PDF\n%%EOF",
        b"\x89PNG\r\n\x1a\nbroken",
        b"<script>evil()</script>",
        b"#!/bin/sh\necho executable",
        b"GIF89a",
        b"",
        image_bytes() + b"<script>evil</script>",
        pdf_bytes(script=True),
        pdf_bytes(encrypted=True),
    ],
)
@pytest.mark.parametrize("action", ["JCC_ADD", "TOWER_PHOTO_ADD"])
def test_invalid_content_rejected(client, db_session, evidence, content, action):
    _, report, token, store = evidence
    assert (
        send(
            client, report, token, action, files=[("pretend.png", content)]
        ).status_code
        == 422
    )
    assert not records(db_session, report)
    assert not store.root.exists()


@pytest.mark.parametrize("count", [1, 5, 6])
def test_photo_count(client, db_session, evidence, count):
    _, report, token, _ = evidence
    result = send(
        client,
        report,
        token,
        "TOWER_PHOTO_ADD",
        files=[(f"p{i}.png", image_bytes()) for i in range(count)],
    )
    assert result.status_code == (200 if count <= 5 else 422)
    assert len(records(db_session, report)) == (count if count <= 5 else 0)
    if count == 5:
        assert (
            send(
                client,
                report,
                token,
                "TOWER_PHOTO_ADD",
                files=[("sixth.png", image_bytes())],
            ).status_code
            == 422
        )
        assert len(records(db_session, report)) == 5


@pytest.mark.parametrize("over", [False, True])
def test_photo_aggregate_exact_boundary(client, db_session, evidence, over):
    _, report, token, _ = evidence
    first = image_bytes(exact_size=PHOTO_BYTES // 2)
    second = image_bytes(exact_size=PHOTO_BYTES // 2 + int(over))
    assert (
        send(
            client, report, token, "TOWER_PHOTO_ADD", files=[("first.png", first)]
        ).status_code
        == 200
    )
    result = send(
        client, report, token, "TOWER_PHOTO_ADD", files=[("second.png", second)]
    )
    assert result.status_code == (422 if over else 200)
    assert sum(r.byte_size for r in records(db_session, report)) == (
        len(first) if over else PHOTO_BYTES
    )


def test_photo_remove_replace_order_size(client, db_session, evidence):
    _, report, token, store = evidence
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_ADD",
            files=[("a.png", image_bytes()), ("b.jpg", image_bytes("JPEG"))],
        ).status_code
        == 200
    )
    a, b = records(db_session, report)
    old_key = a.storage_key
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_REPLACE",
            target_id=a.id,
            files=[("replacement.jpg", image_bytes("JPEG"))],
        ).status_code
        == 200
    )
    rows = records(db_session, report)
    assert [r.display_filename for r in rows] == ["replacement.jpg", "b.jpg"]
    with pytest.raises(FileNotFoundError):
        store.read(old_key)
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_REORDER",
            order=json.dumps([b.id, rows[0].id]),
        ).status_code
        == 200
    )
    assert records(db_session, report)[0].id == b.id
    assert (
        send(client, report, token, "TOWER_PHOTO_REMOVE", target_id=b.id).status_code
        == 200
    )
    assert [r.position for r in records(db_session, report)] == [1]
    html = client.get(f"/ecr/packages/{report.tower.package_id}/attachments").text
    assert "1 of 5" in html and file_size(len(image_bytes('JPEG'))) in html
    assert f"{len(image_bytes('JPEG'))} bytes" not in html


@pytest.mark.parametrize("status", list(EcrReportStatus))
@pytest.mark.parametrize("another_tower", [False, True])
def test_package_wide_all_cells_lock(
    client, db_session, evidence, user_factory, status, another_tower
):
    owner, report, token, _ = evidence
    other = user_factory()
    cell = extra_cell(
        db_session, report, other, status=status, another_tower=another_tower
    )
    result = send(client, report, token, files=[("jcc.pdf", pdf_bytes())])
    allowed = status in (EcrReportStatus.DRAFT, EcrReportStatus.REVIEWED)
    assert result.status_code == (200 if allowed else 403)
    assert report.status is EcrReportStatus.DRAFT and cell.status is status
    assert attachments.editable(db_session, report.tower.package, owner) is allowed


@pytest.mark.parametrize("status", [EcrReportStatus.DRAFT, EcrReportStatus.REVIEWED])
def test_creator_own_reviewed_report_can_edit(client, db_session, evidence, status):
    _, report, token, _ = evidence
    report.status = status
    db_session.commit()
    assert (
        send(client, report, token, files=[("jcc.pdf", pdf_bytes())]).status_code == 200
    )
    db_session.refresh(report)
    assert report.status is status
    # Evidence is optional and does not alter final completeness decisions.
    before = final_errors(report)
    assert send(client, report, token, "JCC_REMOVE").status_code == 200
    assert final_errors(report) == before


@pytest.mark.parametrize(
    "role,related,visible",
    [
        (UserRole.SUPERVISOR, True, True),
        (UserRole.SUPERVISOR, False, False),
        (UserRole.BRANCH_ADMIN, True, True),
        (UserRole.BRANCH_ADMIN, False, False),
        (UserRole.SUPERADMIN, False, True),
    ],
)
def test_private_access_and_read_only_roles(
    client, db_session, evidence, user_factory, branch_factory, role, related, visible
):
    owner, report, token, _ = evidence
    assert (
        send(client, report, token, files=[("jcc.pdf", pdf_bytes())]).status_code == 200
    )
    row = records(db_session, report)[0]
    branch = owner.branch if related else branch_factory(code="ATT-OTHER")
    viewer = user_factory(role=role, branch=branch)
    if related and role is UserRole.SUPERVISOR:
        extra_cell(db_session, report, viewer)
    client.cookies.clear()
    login(client, viewer.employee_id)
    url = f"/ecr/packages/{row.package_id}/attachments"
    page = client.get(url)
    assert page.status_code == (200 if visible else 404)
    assert client.get(f"{url}/{row.id}").status_code == (200 if visible else 404)
    if visible:
        assert "data-attachment-form" not in page.text
        csrf = csrf_from(client.get("/dashboard").text)
        assert send(client, report, csrf, "JCC_REMOVE").status_code == 403
    else:
        assert row.display_filename not in page.text


def test_anonymous_csrf_crafted_ids_and_owner_must_still_own_report(
    client, db_session, evidence, user_factory
):
    owner, report, token, _ = evidence
    assert (
        send(client, report, "wrong", files=[("jcc.pdf", pdf_bytes())]).status_code
        == 403
    )
    assert (
        send(client, report, token, files=[("jcc.pdf", pdf_bytes())]).status_code == 200
    )
    row = records(db_session, report)[0]
    assert (
        client.get(
            f"/ecr/packages/{row.package_id + 100000}/attachments/{row.id}"
        ).status_code
        == 404
    )
    client.cookies.clear()
    assert client.get(
        f"/ecr/packages/{row.package_id}/attachments/{row.id}"
    ).status_code in (303, 401)
    login(client, owner.employee_id)
    other = user_factory()
    report.supervisor_user_id = other.id
    db_session.commit()
    assert send(client, report, token, "JCC_REMOVE").status_code in (403, 404)


@pytest.mark.parametrize(
    "operation",
    ["JCC_REPLACE", "JCC_REMOVE", "TOWER_PHOTO_REPLACE", "TOWER_PHOTO_REMOVE"],
)
def test_db_failure_preserves_prior_reference_and_object(
    client, db_session, evidence, monkeypatch, operation
):
    _, report, token, store = evidence
    kind = "JCC" if operation.startswith("JCC") else "TOWER_PHOTO"
    assert (
        send(
            client, report, token, kind + "_ADD", files=[("old.png", image_bytes())]
        ).status_code
        == 200
    )
    row = records(db_session, report)[0]
    key, row_id = row.storage_key, row.id
    events_before = list(db_session.scalars(select(EcrPackageAuditEvent)))

    def fail_commit():
        raise OperationalError("mock commit", {}, Exception("offline"))

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        result = send(
            client,
            report,
            token,
            operation,
            target_id=row_id if kind == "TOWER_PHOTO" else "",
            files=[("new.jpg", image_bytes("JPEG"))]
            if operation.endswith("REPLACE")
            else None,
        )
    assert result.status_code == 503
    db_session.expire_all()
    assert records(db_session, report)[0].storage_key == key
    assert store.read(key) == image_bytes()
    assert len(list(db_session.scalars(select(EcrPackageAuditEvent)))) == len(
        events_before
    )


def test_cleanup_failure_keeps_committed_replacement(
    client, db_session, evidence, monkeypatch
):
    owner, report, token, store = evidence
    assert (
        send(client, report, token, files=[("old.pdf", pdf_bytes())]).status_code == 200
    )
    old = records(db_session, report)[0].storage_key
    monkeypatch.setattr(
        store, "delete", lambda _: (_ for _ in ()).throw(OSError("mock cleanup"))
    )
    assert (
        send(
            client, report, token, "JCC_REPLACE", files=[("new.pdf", pdf_bytes(2))]
        ).status_code
        == 200
    )
    row = records(db_session, report)[0]
    assert row.storage_key != old and store.read(row.storage_key) == pdf_bytes(2)
    assert store.read(old) == pdf_bytes()
    events = list(
        db_session.scalars(
            select(EcrPackageAuditEvent)
            .where(EcrPackageAuditEvent.package_id == row.package_id)
            .order_by(EcrPackageAuditEvent.id)
        )
    )
    assert [e.action for e in events] == ["JCC_ADD", "JCC_REPLACE"]
    assert all(
        e.actor_user_id == owner.id and e.actor_role == "SUPERVISOR" for e in events
    )
    serialized = " ".join((e.old_value or "") + (e.new_value or "") for e in events)
    assert (
        old not in serialized
        and row.storage_key not in serialized
        and str(store.root) not in serialized
    )


@pytest.mark.parametrize("kind", ["leaf", "namespace", "ancestor", "traversal"])
def test_private_storage_rejects_symlink_substitution_and_traversal(tmp_path, kind):
    store = LocalAttachmentStorage(tmp_path / "private")
    key = store.put(image_bytes(), "image/png")
    assert (store.root / key).stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_bytes(b"not attachment content")
    if kind == "leaf":
        (store.root / key).unlink()
        (store.root / key).symlink_to(secret)
    elif kind == "namespace":
        store.root.rename(store.root.with_name("original"))
        store.root.symlink_to(outside, target_is_directory=True)
    elif kind == "ancestor":
        store.root.parent.rename(tmp_path / "original")
        store.root.parent.symlink_to(outside, target_is_directory=True)
    else:
        key = "../../outside/secret"
    with pytest.raises((OSError, ValueError)):
        store.read(key)
    if kind in ("namespace", "ancestor"):
        with pytest.raises(OSError):
            store.put(image_bytes(), "image/png")
    assert secret.read_bytes() == b"not attachment content"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_content_sniffing_not_client_filename_or_mime(fmt):
    data = validate_content(image_bytes(fmt), "../../evil.exe", allow_pdf=False)
    assert data.media_type == ("image/jpeg" if fmt == "JPEG" else "image/png")
    assert data.filename == "evil.exe"


def test_mixed_jcc_and_stale_reorder_rejected(client, db_session, evidence):
    _, report, token, _ = evidence
    assert (
        send(
            client,
            report,
            token,
            files=[("pdf.pdf", pdf_bytes()), ("image.png", image_bytes())],
        ).status_code
        == 422
    )
    assert (
        send(client, report, token, files=[("page.png", image_bytes())]).status_code
        == 200
    )
    row = records(db_session, report)[0]
    for order in ([], [row.id, row.id], [row.id + 100000], [True]):
        assert (
            send(
                client, report, token, "JCC_REORDER", order=json.dumps(order)
            ).status_code
            == 422
        )
    assert records(db_session, report)[0].id == row.id


def test_large_jcc_does_not_inherit_photo_byte_limit(client, db_session, evidence):
    _, report, token, store = evidence
    raw = image_bytes(exact_size=PHOTO_BYTES + 1)
    assert (
        send(client, report, token, files=[("large-jcc.png", raw)]).status_code == 200
    )
    row = records(db_session, report)[0]
    assert row.byte_size == PHOTO_BYTES + 1 and store.read(row.storage_key) == raw
    assert "0 of 5" in client.get(f"/ecr/packages/{row.package_id}/attachments").text


def test_single_oversized_photo_rejected(client, db_session, evidence):
    _, report, token, _ = evidence
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_ADD",
            files=[("large.png", image_bytes(exact_size=PHOTO_BYTES + 1))],
        ).status_code
        == 422
    )
    assert not records(db_session, report)


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_failed_staging_cleans_new_objects_only(
    client, db_session, evidence, monkeypatch, failure
):
    _, report, token, store = evidence
    assert (
        send(client, report, token, files=[("old.pdf", pdf_bytes())]).status_code == 200
    )
    original_key = records(db_session, report)[0].storage_key
    if failure == "write":
        original = store.put_stream
        count = 0

        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("mock partial staging failure")
            return original(*args)

        monkeypatch.setattr(store, "put_stream", fail_second)
    else:
        original = db_session.flush

        def fail_after_staging(*args, **kwargs):
            if len(list(store.root.iterdir())) > 1:
                raise OperationalError("mock flush", {}, Exception("offline"))
            return original(*args, **kwargs)

        monkeypatch.setattr(db_session, "flush", fail_after_staging)
    result = send(
        client,
        report,
        token,
        "JCC_REPLACE",
        files=[("one.png", image_bytes()), ("two.jpg", image_bytes("JPEG"))],
    )
    assert result.status_code == 503
    assert store.read(original_key) == pdf_bytes()
    assert [p.name for p in store.root.iterdir()] == [original_key]
    assert records(db_session, report)[0].storage_key == original_key


def test_lost_commit_acknowledgement_does_not_delete_committed_object(
    client, db_session, evidence, monkeypatch
):
    _, report, token, store = evidence
    assert (
        send(client, report, token, files=[("old.pdf", pdf_bytes())]).status_code == 200
    )
    old_key = records(db_session, report)[0].storage_key
    original_commit = db_session.commit

    def committed_but_disconnected():
        original_commit()
        raise OperationalError(
            "mock lost commit acknowledgement", {}, Exception("offline")
        )

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", committed_but_disconnected)
        assert (
            send(
                client, report, token, "JCC_REPLACE", files=[("new.pdf", pdf_bytes(2))]
            ).status_code
            == 503
        )
    db_session.expire_all()
    row = records(db_session, report)[0]
    assert row.storage_key != old_key and store.read(row.storage_key) == pdf_bytes(2)
    assert store.read(old_key) == pdf_bytes()
    assert (
        client.get(f"/ecr/packages/{row.package_id}/attachments/{row.id}").status_code
        == 200
    )


def test_attachment_key_collision_preserves_original(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.storage import attachments as storage_module

    store = LocalAttachmentStorage(tmp_path)
    monkeypatch.setattr(storage_module, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    key = store.put(b"original", "image/png")
    with pytest.raises(FileExistsError):
        store.put(b"replacement", "image/png")
    assert store.read(key) == b"original"


def test_historical_branch_attachment_access_after_owner_transfer(
    client, db_session, evidence, user_factory, branch_factory
):
    owner, report, token, _ = evidence
    assert (
        send(client, report, token, files=[("jcc.pdf", pdf_bytes())]).status_code == 200
    )
    historical_branch = report.branch
    owner.branch_id = branch_factory(code="ATT-TRANSFER").id
    db_session.commit()
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=historical_branch)
    client.cookies.clear()
    login(client, admin.employee_id)
    row = records(db_session, report)[0]
    assert (
        client.get(f"/ecr/packages/{row.package_id}/attachments/{row.id}").status_code
        == 200
    )
