"""Immutable submission identity for official reports.

Revision ID: 8b3f1a7c902d
Revises: 6a2c9e4f107b

Legacy submitted/approved identity uses the current related User: no earlier
immutable identity exists. Draft/Reviewed identities remain unsnapshotted.
"""

import sqlalchemy as sa

from alembic import op

revision = "8b3f1a7c902d"
down_revision = "6a2c9e4f107b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ecr_reports", sa.Column("supervisor_name_snapshot", sa.String(150)))
    op.add_column(
        "ecr_reports", sa.Column("supervisor_employee_id_snapshot", sa.String(50))
    )
    # Correlated subqueries also work in the disposable SQLite migration fixture.
    op.execute(
        sa.text("""
        UPDATE ecr_reports SET
          supervisor_name_snapshot = (SELECT full_name FROM users
            WHERE users.id = ecr_reports.supervisor_user_id),
          supervisor_employee_id_snapshot = (SELECT employee_id FROM users
            WHERE users.id = ecr_reports.supervisor_user_id)
        WHERE status IN ('SUBMITTED', 'APPROVED')
    """)
    )


def downgrade():
    op.drop_column("ecr_reports", "supervisor_employee_id_snapshot")
    op.drop_column("ecr_reports", "supervisor_name_snapshot")
