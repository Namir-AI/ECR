"""Independent MySQL transactions verify the package lock, not browser timing."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.branches.models import Branch
from app.db.session import get_engine
from app.ecr.models import EcrPackage, EcrReport, EcrTower
from app.ecr.services import (
    DraftNotEditableError,
    EcrIdentityError,
    create_or_resume_draft,
    get_supervisor_report,
    update_series,
)
from app.users.models import User, UserRole, UserStatus
from tests.test_phase3 import _draft_input


@pytest.mark.parametrize("first_action", ["series_edit", "second_report"])
def test_package_lock_serializes_series_and_second_report(
    password_manager, first_action
):
    engine = get_engine()
    tag = uuid4().hex[:12].upper()
    user_id = package_id = tower_id = None
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with Session(engine, expire_on_commit=False) as seed:
            branch_id = seed.scalar(select(Branch.id).where(Branch.code == "HO-KOL"))
            user = User(
                full_name="Concurrency fixture",
                employee_id=f"RACE-{tag}",
                mobile_number=f"+91{int(tag, 16) % 10**10:010d}",
                password_hash=password_manager.hash("Test-Fixture-123"),
                role=UserRole.SUPERVISOR,
                status=UserStatus.ACTIVE,
                must_change_password=False,
                branch_id=branch_id,
            )
            seed.add(user)
            seed.flush()
            report = create_or_resume_draft(
                seed, user, _draft_input(f"RACE-{tag}")
            ).report
            user_id, package_id, tower_id, report_id = (
                user.id,
                report.tower.package_id,
                report.tower_id,
                report.id,
            )
            seed.commit()

        started, finished = Event(), Event()

        def competing_action():
            with Session(engine, expire_on_commit=False) as other:
                actor = other.get(User, user_id)
                started.set()
                try:
                    if first_action == "series_edit":
                        create_or_resume_draft(
                            other, actor, _draft_input(f"RACE-{tag}", cell_no=2)
                        )
                    else:
                        target = get_supervisor_report(
                            other, report_id, user_id, lock=True
                        )
                        update_series(other, target, actor, "AQ-3800")
                    other.commit()
                    return None
                except EcrIdentityError as exc:
                    other.rollback()
                    return type(exc)
                finally:
                    finished.set()

        with Session(engine, expire_on_commit=False) as first:
            actor = first.get(User, user_id)
            if first_action == "series_edit":
                target = get_supervisor_report(first, report_id, user_id, lock=True)
                update_series(first, target, actor, "AQ-3800")
            else:
                create_or_resume_draft(
                    first, actor, _draft_input(f"RACE-{tag}", cell_no=2)
                )
            future = executor.submit(competing_action)
            assert started.wait(5)
            assert not finished.wait(0.2), (
                "Competing mutation bypassed the package lock"
            )
            first.commit()
        expected = (
            EcrIdentityError if first_action == "series_edit" else DraftNotEditableError
        )
        assert future.result(timeout=10) is expected
        with Session(engine) as verify:
            package = verify.get(EcrPackage, package_id)
            assert package.cooling_tower_series == (
                "AQ-3800" if first_action == "series_edit" else "Series 10"
            )
            ids = list(
                verify.scalars(
                    select(EcrReport.id).where(EcrReport.tower_id == tower_id)
                )
            )
            assert len(ids) == (1 if first_action == "series_edit" else 2)
    finally:
        executor.shutdown(wait=True)
        # Only synthetic records created by this test, identified by exact IDs.
        if user_id is not None:
            with Session(engine) as cleanup:
                cleanup.execute(delete(EcrReport).where(EcrReport.tower_id == tower_id))
                cleanup.execute(delete(EcrTower).where(EcrTower.id == tower_id))
                cleanup.execute(delete(EcrPackage).where(EcrPackage.id == package_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.commit()
