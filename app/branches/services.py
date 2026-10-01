"""Branch lookup and creation services."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.branches.models import Branch
from app.branches.schemas import BranchCreateInput, normalized_branch_name_key


class BranchConflictError(ValueError):
    """Raised when normalized branch identity is already in use."""


class BranchSelectionError(ValueError):
    """Raised when a submitted branch cannot be assigned."""


def list_branches(db: Session) -> list[Branch]:
    """List every branch for global administration and historical filtering."""
    return list(db.scalars(select(Branch).order_by(Branch.name)))


def list_active_branches(db: Session) -> list[Branch]:
    return list(
        db.scalars(
            select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
        )
    )


def get_active_branch(db: Session, branch_id: int) -> Branch:
    branch = db.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.is_active.is_(True))
    )
    if branch is None:
        raise BranchSelectionError("Select a valid active branch.")
    return branch


def get_ho_kolkata(db: Session) -> Branch:
    branch = db.scalar(select(Branch).where(Branch.code == "HO-KOL"))
    if branch is None or not branch.is_active:
        raise BranchSelectionError("The HO-Kolkata branch is unavailable.")
    return branch


def create_branch(db: Session, data: BranchCreateInput) -> Branch:
    normalized_name = normalized_branch_name_key(data.name)
    existing = db.scalar(
        select(Branch).where(
            or_(Branch.code == data.code, Branch.normalized_name == normalized_name)
        )
    )
    if existing is not None:
        field = "code" if existing.code == data.code else "name"
        raise BranchConflictError(f"A branch with that {field} already exists.")
    branch = Branch(
        code=data.code,
        name=data.name,
        normalized_name=normalized_name,
        is_active=True,
    )
    db.add(branch)
    db.flush()
    return branch
