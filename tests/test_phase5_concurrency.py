"""Real independent MySQL transactions for concurrent creation and approval.

Only UUID-isolated test records are committed. Exact FK-ordered test targets are
cleaned in finally; production/historical users and reports are never targeted.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.models import ReportAuditEvent
from app.branches.models import Branch
from app.db.session import get_engine
from app.ecr.models import (
    EcrFanBladeSerial,
    EcrFastenerTorqueRow,
    EcrPackage,
    EcrPage1Technical,
    EcrPage2Technical,
    EcrPage3,
    EcrReport,
    EcrReportStatus,
    EcrTower,
)
from app.ecr.operations import create_cell, tower_access
from app.ecr.services import get_admin_visible_report
from app.ecr.workflow import transition
from app.users.models import User, UserRole, UserStatus
from tests.test_phase3 import _create_report
from tests.test_phase5 import complete_report


@pytest.fixture
def committed_operations(password_manager):
    engine = get_engine()
    tag = uuid4().hex[:10].upper()
    package_id = None
    user_ids = []
    branch_id = None
    try:
        with Session(engine, expire_on_commit=False) as db:
            branch = Branch(
                code=f"P5-{tag}",
                name=f"Phase 5 test {tag}",
                normalized_name=f"phase 5 test {tag}",
                is_active=True,
            )
            db.add(branch)
            db.flush()
            branch_id = branch.id
            for index, role in enumerate(
                (UserRole.SUPERVISOR, UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN)
            ):
                user = User(
                    full_name=f"Phase 5 concurrency test {index}",
                    employee_id=f"P5-{tag}-{index}",
                    mobile_number=f"+91{int(uuid4().hex[:10], 16) % 10**10:010d}",
                    email=f"{tag}-{index}@example.invalid",
                    branch_id=branch_id,
                    password_hash=password_manager.hash("Correct-Horse-123"),
                    role=role,
                    status=UserStatus.ACTIVE,
                    must_change_password=False,
                )
                db.add(user)
                db.flush()
                user_ids.append(user.id)
            owner = db.get(User, user_ids[0])
            report = _create_report(db, owner)
            package_id = report.tower.package_id
            complete_report(db, report)
            report.status = EcrReportStatus.SUBMITTED
            db.commit()
            yield engine, user_ids, report.id, report.tower_id
    finally:
        with Session(engine) as db:
            if package_id is not None:
                tower_ids = list(
                    db.scalars(
                        select(EcrTower.id).where(EcrTower.package_id == package_id)
                    )
                )
                report_ids = list(
                    db.scalars(
                        select(EcrReport.id).where(EcrReport.tower_id.in_(tower_ids))
                    )
                )
                page1_ids = list(
                    db.scalars(
                        select(EcrPage1Technical.id).where(
                            EcrPage1Technical.report_id.in_(report_ids)
                        )
                    )
                )
                db.execute(
                    delete(EcrFanBladeSerial).where(
                        EcrFanBladeSerial.technical_id.in_(page1_ids)
                    )
                )
                for model in (
                    ReportAuditEvent,
                    EcrFastenerTorqueRow,
                    EcrPage1Technical,
                    EcrPage2Technical,
                    EcrPage3,
                ):
                    db.execute(delete(model).where(model.report_id.in_(report_ids)))
                db.execute(delete(EcrReport).where(EcrReport.id.in_(report_ids)))
                db.execute(delete(EcrTower).where(EcrTower.id.in_(tower_ids)))
                db.execute(delete(EcrPackage).where(EcrPackage.id == package_id))
            if user_ids:
                db.execute(delete(User).where(User.id.in_(user_ids)))
            if branch_id is not None:
                db.execute(delete(Branch).where(Branch.id == branch_id))
            db.commit()


def test_concurrent_approvals_one_transition_one_audit(committed_operations):
    engine, users, report_id, _ = committed_operations
    barrier = Barrier(2)

    def approve(actor_id):
        with Session(engine) as db:
            actor = db.get(User, actor_id)
            barrier.wait(timeout=10)
            report = get_admin_visible_report(db, report_id, actor, lock=True)
            try:
                assert transition(db, report, actor, "approve") == {}
                db.commit()
                return "approved"
            except ValueError:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(approve, users[1:]))
    assert sorted(results) == ["approved", "conflict"]
    with Session(engine) as db:
        report = db.get(EcrReport, report_id)
        assert report.status is EcrReportStatus.APPROVED
        assert report.approved_by_user_id in users[1:]
        assert (
            db.scalar(
                select(func.count())
                .select_from(ReportAuditEvent)
                .where(ReportAuditEvent.report_id == report_id)
            )
            == 1
        )


def test_concurrent_cell_creation_resumes_same_draft(committed_operations):
    engine, users, _, tower_id = committed_operations
    barrier = Barrier(2)

    def create(_):
        with Session(engine) as db:
            actor = db.get(User, users[0])
            barrier.wait(timeout=10)
            tower = tower_access(db, tower_id, actor, lock=True)
            report = create_cell(db, tower, actor, 2)
            db.commit()
            return report.id

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, range(2)))
    assert results[0] == results[1]
    with Session(engine) as db:
        report = db.get(EcrReport, results[0])
        assert report.erection_completion_date is None
        assert report.page1 is None and report.page2 is None and report.page3 is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(EcrReport)
                .where(EcrReport.tower_id == tower_id, EcrReport.cell_no == 2)
            )
            == 1
        )
