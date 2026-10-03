"""Page 3 plain text and optional protected customer signature.

Revision ID: 4c6d2e9a103f
Revises: 4b2c8e1f903a
"""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT

from alembic import op

revision = "4c6d2e9a103f"
down_revision = "4b2c8e1f903a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ecr_page3",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "team_leader_report",
            sa.Text().with_variant(MEDIUMTEXT(), "mysql"),
            nullable=True,
        ),
        sa.Column(
            "customer_comment",
            sa.Text().with_variant(MEDIUMTEXT(), "mysql"),
            nullable=True,
        ),
        sa.Column("customer_signature_storage_key", sa.String(64), nullable=True),
        sa.Column(
            "customer_signed_at",
            sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["ecr_reports.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("report_id"),
        sa.CheckConstraint(
            "(customer_signature_storage_key IS NULL AND customer_signed_at IS NULL) OR "
            "(customer_signature_storage_key IS NOT NULL AND customer_signed_at IS NOT NULL)",
            name="signature_timestamp_consistent",
        ),
    )


def downgrade():
    # Only Phase 4C capture is removed. Private files are not destructively erased.
    op.drop_table("ecr_page3")
