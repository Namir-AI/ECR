"""Page 2 Batch C scalars and ordered torque rows.

Revision ID: 4b2c8e1f903a
Revises: 4b1a9c2d7e60
"""

import sqlalchemy as sa

from alembic import op

revision = "4b2c8e1f903a"
down_revision = "4b1a9c2d7e60"
branch_labels = None
depends_on = None

READINGS = tuple(
    f"{end}_{position}"
    for end in ("de", "nde")
    for position in ("top", "right", "bottom", "left")
)


def upgrade():
    for name in ("radial_tir", "axial_tir", *READINGS):
        op.add_column(
            "ecr_page2_technical",
            sa.Column(
                name,
                sa.Numeric(4, 3) if name.endswith("tir") else sa.Numeric(3, 2),
                nullable=True,
            ),
        )
        op.create_check_constraint(
            f"{name}_range", "ecr_page2_technical", f"{name} BETWEEN -1 AND 1"
        )
    op.add_column(
        "ecr_page2_technical", sa.Column("de_nde_unit", sa.String(10), nullable=True)
    )
    op.create_check_constraint(
        "de_nde_unit_choice", "ecr_page2_technical", "de_nde_unit IN ('inches', 'mm')"
    )
    for name in ("vibration_limit_switch", "oil_level_switch"):
        op.add_column(
            "ecr_page2_technical", sa.Column(name, sa.Boolean(), nullable=True)
        )
    op.create_table(
        "ecr_fastener_torque_rows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("diameter", sa.String(250), nullable=True),
        sa.Column("torque", sa.Numeric(38, 18), nullable=True),
        sa.Column("torque_unit", sa.String(10), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["ecr_reports.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "report_id", "category", "sequence_no", name="uq_ecr_torque_order"
        ),
        sa.CheckConstraint(
            "category IN ('FAN_HARDWARE', 'TOWER', 'MECH_HOLD_DOWN')",
            name="category_choice",
        ),
        sa.CheckConstraint("sequence_no > 0", name="sequence_positive"),
        sa.CheckConstraint(
            "torque_unit IN ('ft-lbs', 'Nm')", name="torque_unit_choice"
        ),
    )


def downgrade():
    op.drop_table("ecr_fastener_torque_rows")
    for name in ("radial_tir", "axial_tir", *READINGS):
        op.drop_constraint(
            op.f(f"ck_ecr_page2_technical_{name}_range"),
            "ecr_page2_technical",
            type_="check",
        )
        op.drop_column("ecr_page2_technical", name)
    op.drop_constraint(
        op.f("ck_ecr_page2_technical_de_nde_unit_choice"),
        "ecr_page2_technical",
        type_="check",
    )
    for name in ("de_nde_unit", "vibration_limit_switch", "oil_level_switch"):
        op.drop_column("ecr_page2_technical", name)
