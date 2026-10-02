"""add Page 1 technical draft data

Revision ID: 4a8e2c7d901f
Revises: 3d6a1b8c4e20
Create Date: 2026-10-02 11:57:35.613285
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4a8e2c7d901f"
down_revision: str | Sequence[str] | None = "3d6a1b8c4e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ecr_page1_technical",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("motor_make", sa.String(length=250), nullable=True),
        sa.Column("motor_serial_no", sa.String(length=250), nullable=True),
        sa.Column("motor_hp", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("motor_frame", sa.String(length=250), nullable=True),
        sa.Column("motor_insulation", sa.String(length=250), nullable=True),
        sa.Column("motor_mounting", sa.String(length=20), nullable=True),
        sa.Column("motor_speed", sa.String(length=20), nullable=True),
        sa.Column("motor_rpm", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column(
            "motor_full_load_current", sa.Numeric(precision=38, scale=18), nullable=True
        ),
        sa.Column(
            "motor_current_drawn", sa.Numeric(precision=38, scale=18), nullable=True
        ),
        sa.Column("fan_serial_no", sa.String(length=250), nullable=True),
        sa.Column("fan_diameter_type", sa.String(length=250), nullable=True),
        sa.Column("fan_hardware", sa.String(length=20), nullable=True),
        sa.Column("fan_no_of_blades", sa.BigInteger(), nullable=True),
        sa.Column("fan_pitch_angle", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("fan_hub_cover", sa.String(length=20), nullable=True),
        sa.Column(
            "fan_cylinder_height", sa.Numeric(precision=38, scale=18), nullable=True
        ),
        sa.Column("fan_cylinder_material", sa.String(length=20), nullable=True),
        sa.Column(
            "blade_tip_clearance", sa.Numeric(precision=38, scale=18), nullable=True
        ),
        sa.Column(
            "blade_tip_track_variation",
            sa.Numeric(precision=38, scale=18),
            nullable=True,
        ),
        sa.Column("drive_shaft_series", sa.String(length=250), nullable=True),
        sa.Column("drive_shaft_class", sa.String(length=20), nullable=True),
        sa.Column("drive_shaft_oal", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("drive_shaft_oal_unit", sa.String(length=20), nullable=True),
        sa.Column("drive_shaft_serial_no", sa.String(length=250), nullable=True),
        sa.Column("gearbox_series", sa.String(length=250), nullable=True),
        sa.Column("gearbox_ratio", sa.String(length=250), nullable=True),
        sa.Column("gearbox_serial_no", sa.String(length=250), nullable=True),
        sa.Column("gearbox_model_no", sa.String(length=250), nullable=True),
        sa.Column("fill_type", sa.String(length=250), nullable=True),
        sa.Column("fill_material", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "drive_shaft_oal IS NULL OR drive_shaft_oal_unit IS NOT NULL",
            name=op.f("ck_ecr_page1_technical_oal_has_unit"),
        ),
        sa.CheckConstraint(
            "fan_no_of_blades IS NULL OR fan_no_of_blades > 0",
            name=op.f("ck_ecr_page1_technical_blades_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["ecr_reports.id"],
            name=op.f("fk_ecr_page1_technical_report_id_ecr_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecr_page1_technical")),
        sa.UniqueConstraint("report_id", name=op.f("uq_ecr_page1_technical_report_id")),
    )
    op.create_index(
        op.f("ix_ecr_page1_technical_drive_shaft_serial_no"),
        "ecr_page1_technical",
        ["drive_shaft_serial_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ecr_page1_technical_fan_serial_no"),
        "ecr_page1_technical",
        ["fan_serial_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ecr_page1_technical_gearbox_serial_no"),
        "ecr_page1_technical",
        ["gearbox_serial_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ecr_page1_technical_motor_serial_no"),
        "ecr_page1_technical",
        ["motor_serial_no"],
        unique=False,
    )
    op.create_table(
        "ecr_fan_blade_serials",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("technical_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("serial_no", sa.String(length=250), nullable=False),
        sa.CheckConstraint(
            "position > 0", name=op.f("ck_ecr_fan_blade_serials_position_positive")
        ),
        sa.ForeignKeyConstraint(
            ["technical_id"],
            ["ecr_page1_technical.id"],
            name=op.f("fk_ecr_fan_blade_serials_technical_id_ecr_page1_technical"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecr_fan_blade_serials")),
        sa.UniqueConstraint("technical_id", "position", name="uq_ecr_blade_position"),
    )


def downgrade() -> None:
    # Only Phase 4A capture tables are removed. Existing reports/users are retained.
    op.drop_table("ecr_fan_blade_serials")
    op.drop_index(
        op.f("ix_ecr_page1_technical_motor_serial_no"), table_name="ecr_page1_technical"
    )
    op.drop_index(
        op.f("ix_ecr_page1_technical_gearbox_serial_no"),
        table_name="ecr_page1_technical",
    )
    op.drop_index(
        op.f("ix_ecr_page1_technical_fan_serial_no"), table_name="ecr_page1_technical"
    )
    op.drop_index(
        op.f("ix_ecr_page1_technical_drive_shaft_serial_no"),
        table_name="ecr_page1_technical",
    )
    op.drop_table("ecr_page1_technical")
