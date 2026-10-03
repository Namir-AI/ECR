"""Operational workflow, audit and incomplete Cell Draft dates.

Revision ID: 5e7a2d9c104b
Revises: 4c6d2e9a103f
"""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from alembic import op

revision = "5e7a2d9c104b"
down_revision = "4c6d2e9a103f"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "ecr_reports",
        "erection_completion_date",
        existing_type=sa.Date(),
        nullable=True,
    )
    for name in ("reviewed_at", "submitted_at", "approved_at"):
        op.add_column("ecr_reports", sa.Column(name, sa.DateTime(), nullable=True))
    op.add_column(
        "ecr_reports", sa.Column("approved_by_user_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ecr_reports_approved_by_user_id_users",
        "ecr_reports",
        "users",
        ["approved_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "ecr_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column(
            "old_value", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=True
        ),
        sa.Column(
            "new_value", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["ecr_reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_ecr_audit_events_report_created",
        "ecr_audit_events",
        ["report_id", "created_at"],
    )


def downgrade():
    # Never fabricate a completion date to fit the older schema. Operator must
    # resolve incomplete Cell Drafts before intentionally downgrading Phase 5.
    if op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM ecr_reports WHERE erection_completion_date IS NULL"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade: incomplete Cell Draft dates must be resolved first."
        )
    op.drop_table("ecr_audit_events")
    op.drop_constraint(
        "fk_ecr_reports_approved_by_user_id_users", "ecr_reports", type_="foreignkey"
    )
    for name in ("approved_by_user_id", "approved_at", "submitted_at", "reviewed_at"):
        op.drop_column("ecr_reports", name)
    op.alter_column(
        "ecr_reports",
        "erection_completion_date",
        existing_type=sa.Date(),
        nullable=False,
    )
