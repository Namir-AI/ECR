"""Package-level JCC, ordered evidence files and package audit.

Revision ID: 6a2c9e4f107b
Revises: 5e7a2d9c104b

Downgrade removes ONLY new metadata. Protected objects are deliberately retained
for reviewed reconciliation; migrations must never perform filesystem cleanup.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from alembic import op

revision = "6a2c9e4f107b"
down_revision = "5e7a2d9c104b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ecr_jcc_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id"], ["ecr_packages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("package_id", name="uq_ecr_jcc_package"),
        sa.UniqueConstraint("id", "package_id", name="uq_ecr_jcc_id_package"),
        sa.CheckConstraint("format IN ('PDF', 'IMAGES')", name="jcc_format"),
    )
    op.create_table(
        "ecr_package_attachments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(40), nullable=False),
        sa.Column("display_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(30), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("pdf_page_count", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id"], ["ecr_packages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "package_id"],
            ["ecr_jcc_documents.id", "ecr_jcc_documents.package_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "package_id", "kind", "position", name="uq_ecr_attachment_order"
        ),
        sa.UniqueConstraint("storage_key", name="uq_ecr_attachment_storage"),
        sa.CheckConstraint(
            "position > 0 AND byte_size > 0", name="attachment_size_order"
        ),
        sa.CheckConstraint(
            "(kind = 'JCC' AND document_id IS NOT NULL) OR (kind = 'TOWER_PHOTO' AND document_id IS NULL)",
            name="attachment_scope",
        ),
        sa.CheckConstraint(
            "media_type IN ('application/pdf', 'image/jpeg', 'image/png') AND (kind <> 'TOWER_PHOTO' OR media_type <> 'application/pdf')",
            name="attachment_media",
        ),
        sa.CheckConstraint(
            "(media_type = 'application/pdf' AND pdf_page_count > 0) OR (media_type <> 'application/pdf' AND pdf_page_count IS NULL)",
            name="attachment_pdf_pages",
        ),
    )
    op.create_index(
        "ix_ecr_attachment_document_package",
        "ecr_package_attachments",
        ["document_id", "package_id"],
    )
    op.create_table(
        "ecr_package_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["package_id"], ["ecr_packages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_ecr_package_audit_created",
        "ecr_package_audit_events",
        ["package_id", "created_at"],
    )


def downgrade():
    op.drop_table("ecr_package_audit_events")
    op.drop_table("ecr_package_attachments")
    op.drop_table("ecr_jcc_documents")
