"""add branch hierarchy and scoped administrator roles

Revision ID: 2a7c9e4b1d30
Revises: 8ff13fa2d11c
Create Date: 2026-09-30
"""

from collections.abc import Sequence
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "2a7c9e4b1d30"
down_revision: str | Sequence[str] | None = "8ff13fa2d11c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INITIAL_BRANCHES = (
    ("HO-KOL", "HO-Kolkata"),
    ("DELHI", "Delhi"),
    ("BARODA", "Baroda"),
    ("MUMBAI", "Mumbai"),
    ("BANGALORE", "Bangalore"),
    ("CHENNAI", "Chennai"),
    ("HYDERABAD", "Hyderabad"),
)


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branches")),
        sa.UniqueConstraint("code", name=op.f("uq_branches_code")),
        sa.UniqueConstraint("normalized_name", name=op.f("uq_branches_normalized_name")),
    )
    branches = sa.table(
        "branches",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("normalized_name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime(2026, 9, 30)
    op.bulk_insert(
        branches,
        [
            {
                "code": code,
                "name": name,
                "normalized_name": name.casefold(),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            for code, name in INITIAL_BRANCHES
        ],
    )

    op.add_column("users", sa.Column("branch_id", sa.BigInteger(), nullable=True))
    op.drop_constraint(op.f("ck_users_user_role"), "users", type_="check")
    op.execute("UPDATE users SET role = 'SUPERADMIN' WHERE role = 'ADMIN'")
    op.execute(
        "UPDATE users SET branch_id = "
        "(SELECT id FROM branches WHERE code = 'HO-KOL' LIMIT 1)"
    )
    op.alter_column("users", "branch_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        op.f("fk_users_branch_id_branches"),
        "users",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_users_branch_id"), "users", ["branch_id"], unique=False)
    op.create_index(
        "ix_users_branch_role_status",
        "users",
        ["branch_id", "role", "status"],
        unique=False,
    )
    op.create_check_constraint(
        op.f("ck_users_user_role"),
        "users",
        "`role` IN ('SUPERADMIN', 'BRANCH_ADMIN', 'SUPERVISOR')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_user_role"), "users", type_="check")
    op.execute("UPDATE users SET role = 'ADMIN' WHERE role IN ('SUPERADMIN', 'BRANCH_ADMIN')")
    op.create_check_constraint(
        op.f("ck_users_user_role"),
        "users",
        "`role` IN ('SUPERVISOR', 'ADMIN')",
    )
    # MySQL requires the foreign key to be removed before its supporting
    # index. Inspecting first also makes a retry safe after non-transactional
    # MySQL DDL stops partway through a downgrade.
    bind = op.get_bind()
    foreign_key_names = {
        item["name"] for item in sa.inspect(bind).get_foreign_keys("users")
    }
    branch_foreign_key = op.f("fk_users_branch_id_branches")
    if branch_foreign_key in foreign_key_names:
        op.drop_constraint(branch_foreign_key, "users", type_="foreignkey")

    index_names = {item["name"] for item in sa.inspect(bind).get_indexes("users")}
    if "ix_users_branch_role_status" in index_names:
        op.drop_index("ix_users_branch_role_status", table_name="users")
    branch_index = op.f("ix_users_branch_id")
    if branch_index in index_names:
        op.drop_index(branch_index, table_name="users")
    op.drop_column("users", "branch_id")
    op.drop_table("branches")
