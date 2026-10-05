"""Independent MySQL transactions serialize shared uploads and Cell submission."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_password_ready_user
from app.db.session import get_db_session
from app.ecr import attachment_routes, attachments
from app.ecr.attachment_models import (
    EcrJccDocument,
    EcrPackageAttachment,
    EcrPackageAuditEvent,
)
from app.ecr.attachment_validation import validate_content
from app.ecr.models import EcrPackage, EcrReport, EcrReportStatus
from app.ecr.services import get_supervisor_report
from app.ecr.workflow import transition
from app.main import create_app
from app.storage.attachments import LocalAttachmentStorage
from app.users.models import User
from tests import test_phase5_concurrency as concurrency
from tests.conftest import csrf_from
from tests.test_attachments import image_bytes, pdf_bytes

committed_operations = concurrency.committed_operations


@pytest.fixture
def committed_evidence(committed_operations, tmp_path):
    engine, users, report_id, _ = committed_operations
    store = LocalAttachmentStorage(tmp_path / "private")
    with Session(engine) as db:
        report = db.get(EcrReport, report_id)
        package_id = report.tower.package_id
        report.status = EcrReportStatus.REVIEWED
        db.commit()
    try:
        yield engine, users[0], report_id, package_id, store
    finally:
        # Delete ONLY the UUID-isolated fixture's new metadata, before the parent
        # fixture removes its package. Never sweep application/private storage.
        with Session(engine) as db:
            for model in (EcrPackageAuditEvent, EcrPackageAttachment, EcrJccDocument):
                db.execute(delete(model).where(model.package_id == package_id))
            db.commit()


@pytest.fixture
def independent_attachment_client(committed_evidence, app_settings):
    engine, owner_id, _, package_id, store = committed_evidence
    app = create_app(app_settings)

    def sessions():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    with Session(engine) as db:
        owner = db.get(User, owner_id)
        db.expunge(owner)
    # Authentication itself has separate integration coverage. This fixture
    # isolates the real HTTP parsing/streaming/publication with independent DB
    # transactions, rather than the ordinary shared savepoint TestClient fixture.
    app.dependency_overrides[get_db_session] = sessions
    app.dependency_overrides[require_password_ready_user] = lambda: owner
    app.state.attachment_storage = store
    with TestClient(app) as client:
        url = f"/ecr/packages/{package_id}/attachments"
        token = csrf_from(client.get(url).text)
        yield client, url, token


@pytest.mark.parametrize("phase", ["reception", "validation", "staging"])
def test_slow_upload_has_no_mutation_locks_and_submission_wins(
    committed_evidence, independent_attachment_client, monkeypatch, phase
):
    engine, owner_id, report_id, package_id, store = committed_evidence
    client, url, token = independent_attachment_client
    paused, resume = Event(), Event()

    def pause():
        paused.set()
        assert resume.wait(timeout=10)

    if phase == "reception":
        original = attachment_routes.attachment_form

        async def slow(request):
            await run_in_threadpool(pause)
            return await original(request)

        monkeypatch.setattr(attachment_routes, "attachment_form", slow)
    elif phase == "validation":
        original = attachment_routes.validate_stream

        def slow(*args, **kwargs):
            pause()
            return original(*args, **kwargs)

        monkeypatch.setattr(attachment_routes, "validate_stream", slow)
    else:
        original = store.put_stream

        def slow(*args):
            key = original(*args)
            pause()
            return key

        monkeypatch.setattr(store, "put_stream", slow)

    def upload():
        return client.post(
            url,
            data={"action": "JCC_ADD"},
            files={"files": ("jcc.pdf", pdf_bytes())},
            headers={"X-CSRF-Token": token},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(upload)
        assert paused.wait(timeout=10)
        try:
            with Session(engine) as db:
                owner = db.get(User, owner_id)
                # A second transaction can lock package AND all Cell statuses
                # during body reception, parsing and storage staging.
                assert (
                    db.scalar(
                        select(EcrPackage)
                        .where(EcrPackage.id == package_id)
                        .with_for_update(nowait=True)
                    )
                    is not None
                )
                assert (
                    db.scalar(
                        select(EcrReport)
                        .where(EcrReport.id == report_id)
                        .with_for_update(nowait=True)
                    )
                    is not None
                )
                package = attachments.visible_package(db, package_id, owner, lock=True)
                assert attachments.editable(db, package, owner, lock=True)
                report = get_supervisor_report(db, report_id, owner_id, lock=True)
                assert transition(db, report, owner, "submit") == {}
                db.commit()
        finally:
            resume.set()
        assert pending.result(timeout=15).status_code == 403
    with Session(engine) as db:
        assert db.get(EcrReport, report_id).status is EcrReportStatus.SUBMITTED
        assert not attachments.file_records(db, package_id)
    assert not store.root.exists() or not list(store.root.iterdir())


def test_http_publication_lock_commits_before_waiting_submission(
    committed_evidence, independent_attachment_client, monkeypatch
):
    engine, owner_id, report_id, package_id, store = committed_evidence
    client, url, token = independent_attachment_client
    held, release, submit_started, submitted = Event(), Event(), Event(), Event()
    original = attachments.editable

    def locked(db, package, actor, *, lock=False):
        if lock:
            held.set()
            assert release.wait(timeout=10)
        return original(db, package, actor, lock=lock)

    monkeypatch.setattr(attachments, "editable", locked)

    def upload():
        return client.post(
            url,
            data={"action": "JCC_ADD"},
            files={"files": ("jcc.pdf", pdf_bytes())},
            headers={"X-CSRF-Token": token},
        )

    def submit():
        with Session(engine) as db:
            owner = db.get(User, owner_id)
            submit_started.set()
            report = get_supervisor_report(db, report_id, owner_id, lock=True)
            assert transition(db, report, owner, "submit") == {}
            db.commit()
            submitted.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        upload_result = pool.submit(upload)
        assert held.wait(timeout=10)
        submit_result = pool.submit(submit)
        assert submit_started.wait(timeout=10)
        try:
            assert not submitted.wait(timeout=0.2)
        finally:
            release.set()
        assert upload_result.result(timeout=15).status_code == 200
        submit_result.result(timeout=15)
    with Session(engine) as db:
        assert db.get(EcrReport, report_id).status is EcrReportStatus.SUBMITTED
        rows = attachments.file_records(db, package_id)
        assert len(rows) == 1
        assert store.read(rows[0].storage_key) == pdf_bytes()


def test_concurrent_photo_adds_cannot_exceed_aggregate_size(committed_evidence):
    engine, owner_id, _, package_id, store = committed_evidence
    barrier = Barrier(2)
    initial = attachments.stage(
        store,
        validate_content(
            image_bytes(exact_size=2_500_000), "initial.png", allow_pdf=False
        ),
    )
    with Session(engine) as db:
        owner = db.get(User, owner_id)
        attachments.publish(db, package_id, owner, store, "TOWER_PHOTO_ADD", [initial])

    def add():
        upload = attachments.stage(
            store,
            validate_content(
                image_bytes(exact_size=2_500_000), "new.png", allow_pdf=False
            ),
        )
        with Session(engine) as db:
            owner = db.get(User, owner_id)
            barrier.wait(timeout=10)
            try:
                attachments.publish(
                    db, package_id, owner, store, "TOWER_PHOTO_ADD", [upload]
                )
                return "saved"
            except ValueError:
                return "limit"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: add(), range(2))) == ["limit", "saved"]
    with Session(engine) as db:
        rows = attachments.file_records(db, package_id)
        assert len(rows) == 2
        assert sum(r.byte_size for r in rows) == 5_000_000
        assert len(list(store.root.iterdir())) == 2


def test_concurrent_photo_adds_cannot_exceed_five(committed_evidence):
    engine, owner_id, _, package_id, store = committed_evidence
    uploads = [
        attachments.stage(
            store, validate_content(image_bytes(), "photo.png", allow_pdf=False)
        )
        for _ in range(4)
    ]
    with Session(engine) as db:
        owner = db.get(User, owner_id)
        package = attachments.visible_package(db, package_id, owner, lock=True)
        assert attachments.editable(db, package, owner, lock=True)
        attachments.mutate(db, package, owner, store, "TOWER_PHOTO_ADD", uploads)
    barrier = Barrier(2)

    def add():
        upload = attachments.stage(
            store, validate_content(image_bytes(), "photo.png", allow_pdf=False)
        )
        with Session(engine) as db:
            owner = db.get(User, owner_id)
            barrier.wait(timeout=10)
            package = attachments.visible_package(db, package_id, owner, lock=True)
            assert attachments.editable(db, package, owner, lock=True)
            try:
                attachments.mutate(
                    db, package, owner, store, "TOWER_PHOTO_ADD", [upload]
                )
                return "saved"
            except ValueError:
                return "limit"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: add(), range(2))) == ["limit", "saved"]
    with Session(engine) as db:
        assert len(attachments.file_records(db, package_id)) == 5


@pytest.mark.parametrize("first", ["attachment", "submit"])
def test_submission_and_mutation_share_package_lock(committed_evidence, first):
    engine, owner_id, report_id, package_id, store = committed_evidence
    held, second_started, second_acquired, release = Event(), Event(), Event(), Event()

    def act(kind, is_first):
        uploads = (
            [
                attachments.stage(
                    store, validate_content(image_bytes(), "photo.png", allow_pdf=False)
                )
            ]
            if kind == "attachment"
            else []
        )
        with Session(engine) as db:
            owner = db.get(User, owner_id)
            if not is_first:
                second_started.set()
            if kind == "submit":
                report = get_supervisor_report(db, report_id, owner_id, lock=True)
            else:
                package = attachments.visible_package(db, package_id, owner, lock=True)
            if is_first:
                held.set()
                assert release.wait(timeout=10)
            else:
                second_acquired.set()
            if kind == "submit":
                assert transition(db, report, owner, "submit") == {}
                db.commit()
                return "submitted"
            if not attachments.editable(db, package, owner, lock=True):
                db.rollback()
                attachments.cleanup(store, [u.storage_key for u in uploads])
                return "locked"
            attachments.mutate(
                db,
                package,
                owner,
                store,
                "TOWER_PHOTO_ADD",
                uploads,
            )
            return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(act, first, True)
        assert held.wait(timeout=10)
        following = pool.submit(
            act, "submit" if first == "attachment" else "attachment", False
        )
        assert second_started.wait(timeout=10)
        try:
            assert not second_acquired.wait(timeout=0.2)
        finally:
            release.set()
        outcomes = {initial.result(timeout=15), following.result(timeout=15)}
    assert outcomes == (
        {"saved", "submitted"} if first == "attachment" else {"submitted", "locked"}
    )
    with Session(engine) as db:
        assert db.get(EcrReport, report_id).status is EcrReportStatus.SUBMITTED
        assert len(attachments.file_records(db, package_id)) == (
            1 if first == "attachment" else 0
        )
