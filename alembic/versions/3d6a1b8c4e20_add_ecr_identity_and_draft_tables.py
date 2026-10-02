"""add ECR identity and Draft tables

Revision ID: 3d6a1b8c4e20
Revises: 2a7c9e4b1d30
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "3d6a1b8c4e20"
down_revision: str | Sequence[str] | None = "2a7c9e4b1d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ecr_packages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cooling_tower_serial_no", sa.String(length=100), nullable=False),
        sa.Column("normalized_serial_no", sa.String(length=100), nullable=False),
        sa.Column("customer", sa.String(length=200), nullable=False),
        sa.Column("customer_order_no", sa.String(length=100), nullable=True),
        sa.Column("cooling_tower_series", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("place_of_installation", sa.String(length=250), nullable=False),
        sa.Column("multiple_towers", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ecr_packages_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecr_packages")),
        sa.UniqueConstraint(
            "normalized_serial_no",
            name=op.f("uq_ecr_packages_normalized_serial_no"),
        ),
    )
    op.create_index(
        op.f("ix_ecr_packages_created_by_user_id"),
        "ecr_packages",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "ecr_towers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("tower_suffix", sa.String(length=20), nullable=True),
        sa.Column("normalized_suffix_key", sa.String(length=20), nullable=False),
        sa.Column("declared_no_of_cells", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "declared_no_of_cells IS NULL OR declared_no_of_cells > 0",
            name=op.f("ck_ecr_towers_declared_no_of_cells_positive"),
        ),
        sa.CheckConstraint(
            "(tower_suffix IS NULL AND normalized_suffix_key = '') OR "
            "(tower_suffix IS NOT NULL AND normalized_suffix_key <> '')",
            name=op.f("ck_ecr_towers_suffix_key_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["ecr_packages.id"],
            name=op.f("fk_ecr_towers_package_id_ecr_packages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecr_towers")),
        sa.UniqueConstraint(
            "package_id",
            "normalized_suffix_key",
            name="uq_ecr_towers_package_suffix_key",
        ),
    )

    op.create_table(
        "ecr_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tower_id", sa.BigInteger(), nullable=False),
        sa.Column("cell_no", sa.BigInteger(), nullable=False),
        sa.Column("supervisor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("erection_start_date", sa.Date(), nullable=True),
        sa.Column("erection_completion_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "REVIEWED",
                "SUBMITTED",
                "APPROVED",
                name="ecr_report_status",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "cell_no > 0",
            name=op.f("ck_ecr_reports_cell_no_positive"),
        ),
        sa.CheckConstraint(
            "`status` IN ('DRAFT', 'REVIEWED', 'SUBMITTED', 'APPROVED')",
            name=op.f("ck_ecr_reports_ecr_report_status"),
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
            name=op.f("fk_ecr_reports_branch_id_branches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supervisor_user_id"],
            ["users.id"],
            name=op.f("fk_ecr_reports_supervisor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tower_id"],
            ["ecr_towers.id"],
            name=op.f("fk_ecr_reports_tower_id_ecr_towers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecr_reports")),
        sa.UniqueConstraint(
            "tower_id",
            "cell_no",
            name="uq_ecr_reports_tower_cell",
        ),
    )
    op.create_index(
        "ix_ecr_reports_branch_updated",
        "ecr_reports",
        ["branch_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ecr_reports_status"),
        "ecr_reports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_ecr_reports_supervisor_updated",
        "ecr_reports",
        ["supervisor_user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("ecr_reports")
    op.drop_table("ecr_towers")
    op.drop_table("ecr_packages")
