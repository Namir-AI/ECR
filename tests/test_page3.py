"""Page 3 persistence, image validation, privacy and transaction safety."""

import base64
from datetime import datetime
from io import BytesIO

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app.ecr import page3
from app.ecr.models import EcrPage3, EcrReportStatus
from app.storage import LocalProtectedStorage
from app.users.models import UserRole, UserStatus
from tests.conftest import csrf_from, login
from tests.test_phase3 import _create_report


def png(*, blank=False, transparent=False, format="PNG", size=(900, 300)):
    image = Image.new("RGBA", size, (255, 255, 255, 0 if transparent else 255))
    if not blank:
        ImageDraw.Draw(image).line(
            [(25, 150), (100, 80), (200, 200), (600, 150)], fill="black", width=4
        )
    output = BytesIO()
    image.convert("RGB").save(output, format=format) if format != "PNG" else image.save(
        output, format=format
    )
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def page3_draft(client, db_session, user_factory, tmp_path):
    supervisor = user_factory()
    report = _create_report(db_session, supervisor)
    client.app.state.protected_storage = LocalProtectedStorage(tmp_path / "private")
    login(client, supervisor.employee_id)
    token = csrf_from(client.get(f"/ecr/reports/{report.id}/edit").text)
    return supervisor, report, token


def text_save(client, report, token, **fields):
    return client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "erection_completion_date": "2026-10-02",
            "page3_present": "1",
            **fields,
        },
    )


def sign(client, report, token, **fields):
    return client.post(
        f"/ecr/reports/{report.id}/signature",
        json=fields or {"signature_png": png()},
        headers={"X-CSRF-Token": token},
    )


@pytest.mark.parametrize("field", ["team_leader_report", "customer_comment"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        " \n ",
        "\nFirst paragraph\n\nSecond <script>alert(1)</script> & Ω\n",
        "a" * 20000,
    ],
)
def test_text_partial_multiline_safe_roundtrip(
    client, db_session, page3_draft, field, value
):
    _, report, token = page3_draft
    result = text_save(client, report, token, **{field: value})
    assert result.status_code == 200, result.text
    expected = value if value.strip() else None
    db_session.expire_all()
    assert getattr(report.page3, field) == expected
    assert result.json()["values"]["page3"][field] == (expected or "")
    readonly = client.get(f"/ecr/reports/{report.id}").text
    assert "<script>alert(1)</script>" not in readonly
    if "<script>" in value:
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in readonly


def test_text_identity_metadata_no_duplicate_name(
    client, db_session, page3_draft, user_factory
):
    owner, report, token = page3_draft
    assert page3.Page3DraftInput.model_fields["team_leader_report"].json_schema_extra[
        "final_required"
    ]
    assert not page3.Page3DraftInput.model_fields["customer_comment"].json_schema_extra[
        "final_required"
    ]
    other = user_factory()
    assert (
        text_save(
            client,
            report,
            token,
            supervisor_user_id=other.id,
            branch_id=other.branch_id,
            customer_signed_at="2000-01-01",
            customer_signature_storage_key="evil",
            full_name="Evil",
        ).status_code
        == 200
    )
    db_session.refresh(report)
    assert (
        report.supervisor_user_id == owner.id
        and report.page3.customer_signed_at is None
    )
    html = client.get(f"/ecr/reports/{report.id}/edit").text
    assert "Erected / Commissioned by" in html and owner.full_name.upper() in html
    assert 'name="full_name"' not in html and 'name="customer_signed_at"' not in html
    assert report.page3.customer_signature_storage_key is None


def test_crlf_and_technical_limit(client, db_session, page3_draft):
    _, report, token = page3_draft
    assert (
        text_save(client, report, token, team_leader_report="a\r\nb\rc").status_code
        == 200
    )
    db_session.expire_all()
    assert report.page3.team_leader_report == "a\nb\nc"
    assert (
        text_save(client, report, token, team_leader_report="x" * 100001).status_code
        == 422
    )
    db_session.expire_all()
    assert report.page3.team_leader_report == "a\nb\nc"


@pytest.mark.parametrize(
    "representation",
    [
        png(blank=True),
        png(blank=True, transparent=True),
        "",
        "data:image/png;base64,broken",
        png(format="JPEG"),
        "data:image/svg+xml;base64,PHN2Zy8+",
    ],
)
def test_empty_malformed_or_non_png_not_saved(
    client, db_session, page3_draft, representation
):
    _, report, token = page3_draft
    response = sign(client, report, token, signature_png=representation)
    assert response.status_code == 422 and not response.json()["ok"]
    db_session.expire_all()
    assert report.page3 is None
    assert not list(client.app.state.protected_storage.root.glob("*.png"))


def test_signature_save_replace_clear_timestamp_private_files(
    client, db_session, page3_draft, monkeypatch
):
    _, report, token = page3_draft
    from app.ecr import routes

    monkeypatch.setattr(
        routes,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-03T10:00:00.123456+00:00").replace(
            tzinfo=None
        ),
    )
    assert (
        text_save(
            client,
            report,
            token,
            team_leader_report="Keep this",
            customer_comment="Keep too",
        ).status_code
        == 200
    )
    result = sign(client, report, token)
    assert (
        result.status_code == 200
        and result.json()["signature"]["signed_at"] == "2026-10-03T10:00:00.123456Z"
    )
    db_session.expire_all()
    key = report.page3.customer_signature_storage_key
    path = client.app.state.protected_storage._path(key)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    retrieval = client.get(f"/ecr/reports/{report.id}/signature")
    assert (
        retrieval.status_code == 200
        and retrieval.headers["content-type"] == "image/png"
    )
    assert "no-store" in retrieval.headers["cache-control"] and key not in result.text
    with Image.open(BytesIO(retrieval.content)) as image:
        assert image.format == "PNG" and image.mode == "RGB"
    assert client.get(f"/static/{key}").status_code == 404
    assert "Signed on:" in client.get(f"/ecr/reports/{report.id}").text
    monkeypatch.setattr(
        routes,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-03T11:00:00+00:00").replace(
            tzinfo=None
        ),
    )
    assert sign(client, report, token).status_code == 200
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key != key and not path.exists()
    assert report.page3.customer_signed_at.hour == 11
    replaced = client.app.state.protected_storage._path(
        report.page3.customer_signature_storage_key
    )
    response = client.delete(
        f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": token}
    )
    assert (
        response.status_code == 200
        and response.json()["signature"]["signed_at"] is None
    )
    db_session.expire_all()
    assert (
        report.page3.customer_signed_at is None
        and report.page3.customer_signature_storage_key is None
    )
    assert (
        report.page3.team_leader_report == "Keep this"
        and report.page3.customer_comment == "Keep too"
    )
    assert (
        not replaced.exists()
        and client.get(f"/ecr/reports/{report.id}/signature").status_code == 404
    )
    assert (
        client.delete(
            f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": token}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"signed_at": "2000-01-01"},
        {"filename": "evil.png"},
        {"has_signature": True},
        {"report_id": 1},
        {"supervisor_user_id": 1},
    ],
)
def test_no_client_metadata_or_flags(client, page3_draft, extra):
    _, report, token = page3_draft
    assert sign(client, report, token, signature_png=png(), **extra).status_code == 422


def test_file_upload_and_large_image_rejected(client, page3_draft):
    _, report, token = page3_draft
    assert (
        client.post(
            f"/ecr/reports/{report.id}/signature",
            files={"file": ("signature.png", b"fake")},
            headers={"X-CSRF-Token": token},
        ).status_code
        == 415
    )
    assert (
        sign(client, report, token, signature_png=png(size=(2049, 300))).status_code
        == 422
    )
    assert (
        sign(
            client,
            report,
            token,
            signature_png="data:image/png;base64," + "A" * page3.JSON_LIMIT,
        ).status_code
        == 413
    )


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_signature_csrf(client, page3_draft, method):
    _, report, _ = page3_draft
    assert (
        client.request(
            method, f"/ecr/reports/{report.id}/signature", json={"signature_png": png()}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "viewer", ["anonymous", "unrelated", "other_branch", "own_admin", "superadmin"]
)
def test_signature_privacy_and_report_identity(
    client, db_session, page3_draft, user_factory, branch_factory, viewer
):
    owner, report, token = page3_draft
    assert sign(client, report, token).status_code == 200
    client.cookies.clear()
    authorized = viewer in {"own_admin", "superadmin"}
    if viewer != "anonymous":
        role = {
            "own_admin": UserRole.BRANCH_ADMIN,
            "other_branch": UserRole.BRANCH_ADMIN,
            "superadmin": UserRole.SUPERADMIN,
        }.get(viewer, UserRole.SUPERVISOR)
        user = user_factory(
            role=role,
            branch=branch_factory(code="MUMBAI")
            if viewer == "other_branch"
            else report.branch,
        )
        login(client, user.employee_id)
    response = client.get(f"/ecr/reports/{report.id}/signature")
    assert response.status_code == (
        200 if authorized else 303 if viewer == "anonymous" else 404
    )
    assert (
        client.get(f"/ecr/reports/{report.id}/signature-state").status_code
        == response.status_code
    )
    if authorized:
        html = client.get(f"/reports/{report.id}").text
        assert owner.full_name in html and owner.full_name.upper() in html
        assert "signature-pad" not in html and "Save Signature" not in html
        assert (
            client.post(
                f"/ecr/reports/{report.id}/signature",
                json={"signature_png": png()},
                headers={"X-CSRF-Token": token},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": token}
            ).status_code
            == 403
        )


def test_nonowner_and_non_draft_writes_blocked(
    client, db_session, page3_draft, user_factory
):
    owner, report, token = page3_draft
    other = user_factory()
    client.cookies.clear()
    login(client, other.employee_id)
    other_token = csrf_from(client.get("/dashboard").text)
    assert (
        text_save(client, report, other_token, team_leader_report="wrong").status_code
        == 404
    )
    assert sign(client, report, other_token).status_code == 404
    assert (
        client.delete(
            f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": other_token}
        ).status_code
        == 404
    )
    client.cookies.clear()
    login(client, owner.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    report.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    assert (
        text_save(client, report, token, team_leader_report="wrong").status_code == 409
    )
    assert sign(client, report, token).status_code == 409
    assert (
        client.delete(
            f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": token}
        ).status_code
        == 409
    )


def test_schema_invariants_and_historical_identity(client, db_session, page3_draft):
    owner, report, token = page3_draft
    assert sign(client, report, token).status_code == 200
    owner.status = UserStatus.DISABLED
    db_session.commit()
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key is not None
    columns = inspect(db_session.bind).get_columns("ecr_page3")
    assert all(
        c["nullable"]
        for c in columns
        if c["name"] not in {"id", "report_id", "created_at", "updated_at"}
    )
    assert (
        str(next(c["type"] for c in columns if c["name"] == "team_leader_report"))
        == "MEDIUMTEXT"
    )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(EcrPage3(report_id=report.id))
        db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(EcrPage3(report_id=2**62))
        db_session.flush()
    with pytest.raises((IntegrityError, OperationalError)), db_session.begin_nested():
        report.page3.customer_signed_at = None
        db_session.flush()


def test_save_failure_preserves_prior_signature(
    client, db_session, page3_draft, monkeypatch
):
    _, report, token = page3_draft
    assert sign(client, report, token).status_code == 200
    key = report.page3.customer_signature_storage_key
    with monkeypatch.context() as patch:
        patch.setattr(
            db_session,
            "commit",
            lambda: (_ for _ in ()).throw(
                OperationalError("commit", {}, Exception("simulated"))
            ),
        )
        assert sign(client, report, token).status_code == 503
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key == key
    assert client.get(f"/ecr/reports/{report.id}/signature").status_code == 200


def test_private_store_path_safety(tmp_path):
    store = LocalProtectedStorage(tmp_path)
    for key in ("../secret", "/etc/passwd", "a.png", "a" * 32 + ".jpg"):
        with pytest.raises(ValueError):
            store.read(key)
    key = store.put(b"test")
    assert store.read(key) == b"test"
    store.delete(key)
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    path = store._path("a" * 32 + ".png")
    path.symlink_to(outside)
    with pytest.raises(OSError):
        store.read(path.name)


def test_old_forms_and_empty_replacement_preserve_page3(
    client, db_session, page3_draft
):
    _, report, token = page3_draft
    assert (
        text_save(
            client,
            report,
            token,
            team_leader_report="Stored",
            customer_comment="Comment",
        ).status_code
        == 200
    )
    assert sign(client, report, token).status_code == 200
    key, signed_at = (
        report.page3.customer_signature_storage_key,
        report.page3.customer_signed_at,
    )
    assert (
        client.post(
            f"/ecr/reports/{report.id}/autosave",
            data={"csrf_token": token, "erection_completion_date": "2026-10-02"},
        ).status_code
        == 200
    )
    assert sign(client, report, token, signature_png=png(blank=True)).status_code == 422
    db_session.expire_all()
    assert (
        report.page3.team_leader_report == "Stored"
        and report.page3.customer_comment == "Comment"
    )
    assert (
        report.page3.customer_signature_storage_key == key
        and report.page3.customer_signed_at == signed_at
    )


def test_text_csrf_and_signature_storage_failure(
    client, db_session, page3_draft, monkeypatch
):
    _, report, token = page3_draft
    assert (
        text_save(client, report, "bad-token", team_leader_report="bad").status_code
        == 403
    )
    store = client.app.state.protected_storage

    def unavailable(_content):
        raise OSError("simulated private storage error")

    monkeypatch.setattr(store, "put", unavailable)
    result = sign(client, report, token)
    assert result.status_code == 503 and not result.json()["ok"]
    db_session.expire_all()
    assert report.page3 is None


def test_signature_clear_commit_failure_preserves_file(
    client, db_session, page3_draft, monkeypatch
):
    _, report, token = page3_draft
    assert sign(client, report, token).status_code == 200
    key, signed_at = (
        report.page3.customer_signature_storage_key,
        report.page3.customer_signed_at,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            db_session,
            "commit",
            lambda: (_ for _ in ()).throw(
                OperationalError("commit", {}, Exception("simulated"))
            ),
        )
        assert (
            client.delete(
                f"/ecr/reports/{report.id}/signature", headers={"X-CSRF-Token": token}
            ).status_code
            == 503
        )
    db_session.expire_all()
    assert (
        report.page3.customer_signature_storage_key == key
        and report.page3.customer_signed_at == signed_at
    )
    assert client.app.state.protected_storage._path(key).exists()


def test_signature_historical_branch_after_transfer(
    client, db_session, page3_draft, user_factory, branch_factory
):
    owner, report, token = page3_draft
    assert sign(client, report, token).status_code == 200
    historical = report.branch
    destination = branch_factory(code="MUMBAI")
    owner.branch_id = destination.id
    db_session.commit()
    db_session.expire_all()
    assert report.branch_id == historical.id
    for branch, expected in [(historical, 200), (destination, 404)]:
        admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=branch)
        client.cookies.clear()
        login(client, admin.employee_id)
        assert client.get(f"/ecr/reports/{report.id}/signature").status_code == expected


def test_production_requires_persistent_absolute_storage(client, page3_draft, tmp_path):
    from starlette.requests import Request

    from app.core.config import AppSettings
    from app.storage import get_protected_storage

    app = client.app
    del app.state.protected_storage
    app.state.settings = AppSettings(_env_file=None, app_env="production")
    request = Request({"type": "http", "app": app})
    with pytest.raises(OSError, match="absolute persistent"):
        get_protected_storage(request)
    app.state.settings = AppSettings(
        _env_file=None, app_env="production", storage_root=tmp_path
    )
    assert isinstance(get_protected_storage(request), LocalProtectedStorage)


def test_exclusive_key_collision_does_not_delete_saved_object(tmp_path, monkeypatch):
    from uuid import UUID

    from app.storage import protected

    store = LocalProtectedStorage(tmp_path)
    monkeypatch.setattr(
        protected, "uuid4", lambda: UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    )
    key = store.put(b"original")
    with pytest.raises(FileExistsError):
        store.put(b"replacement")
    assert store.read(key) == b"original"
