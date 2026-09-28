"""Create the Phase 1 migration baseline.

Revision ID: 20260928_01
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

revision: str = "20260928_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish the migration baseline without business tables."""
    pass


def downgrade() -> None:
    """Return to the pre-baseline revision without business tables."""
    pass
