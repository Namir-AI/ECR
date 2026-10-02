"""Page 2 Batch A+B nullable scalar capture.

Revision ID: 4b1a9c2d7e60
Revises: 4a8e2c7d901f
"""

import sqlalchemy as sa

from alembic import op

revision = "4b1a9c2d7e60"
down_revision = "4a8e2c7d901f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ecr_page2_technical",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("eliminator_type", sa.String(250), nullable=True),
        sa.Column("fc_valve_diameter", sa.Numeric(38, 18), nullable=True),
        sa.Column("fc_valve_count", sa.BigInteger(), nullable=True),
        sa.Column("nozzle_type", sa.String(250), nullable=True),
        sa.Column("nozzle_count_per_cell", sa.BigInteger(), nullable=True),
        sa.Column("nozzle_part_no", sa.String(250), nullable=True),
        sa.Column("bearing_housing_type", sa.String(250), nullable=True),
        sa.Column("bearing_housing_serial_no", sa.String(250), nullable=True),
        sa.Column("belt_type", sa.String(250), nullable=True),
        sa.Column("belt_section_length", sa.String(250), nullable=True),
        sa.Column("small_pulley_od", sa.Numeric(38, 18), nullable=True),
        sa.Column("large_pulley_od", sa.Numeric(38, 18), nullable=True),
        sa.Column("belts_used_count", sa.BigInteger(), nullable=True),
        sa.Column("uniform_belt_tension", sa.String(20), nullable=True),
        sa.Column("pulley_construction", sa.String(30), nullable=True),
        sa.Column("oil_type", sa.String(250), nullable=True),
        sa.Column("oil_level_checked", sa.String(20), nullable=True),
        sa.Column("oil_seal_leakage", sa.String(20), nullable=True),
        sa.Column("general_tower_hardware", sa.String(20), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["ecr_reports.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("report_id"),
        sa.CheckConstraint("fc_valve_diameter >= 0", name="valve_diameter_nonnegative"),
        sa.CheckConstraint("small_pulley_od >= 0", name="small_pulley_nonnegative"),
        sa.CheckConstraint("large_pulley_od >= 0", name="large_pulley_nonnegative"),
        sa.CheckConstraint("fc_valve_count > 0", name="valve_count_positive"),
        sa.CheckConstraint("nozzle_count_per_cell > 0", name="nozzle_count_positive"),
        sa.CheckConstraint("belts_used_count > 0", name="belt_count_positive"),
    )


def downgrade() -> None:
    # Only Batch A+B is removed; accepted Page 1 and identity tables are untouched.
    op.drop_table("ecr_page2_technical")
