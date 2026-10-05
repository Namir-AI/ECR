"""Central model imports used by Alembic metadata discovery."""

from app.audit.models import ReportAuditEvent
from app.auth.models import UserSession
from app.branches.models import Branch
from app.ecr.attachment_models import (
    EcrJccDocument,
    EcrPackageAttachment,
    EcrPackageAuditEvent,
)
from app.ecr.models import (
    EcrFanBladeSerial,
    EcrFastenerTorqueRow,
    EcrPackage,
    EcrPage1Technical,
    EcrPage2Technical,
    EcrPage3,
    EcrReport,
    EcrTower,
)
from app.users.models import PasswordResetRequest, User

__all__ = [
    "Branch",
    "EcrFanBladeSerial",
    "EcrFastenerTorqueRow",
    "EcrJccDocument",
    "EcrPackage",
    "EcrPackageAttachment",
    "EcrPackageAuditEvent",
    "EcrPage1Technical",
    "EcrPage2Technical",
    "EcrPage3",
    "EcrReport",
    "EcrTower",
    "PasswordResetRequest",
    "ReportAuditEvent",
    "User",
    "UserSession",
]
