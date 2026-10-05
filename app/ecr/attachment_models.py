"""Package evidence metadata; object content lives only in protected storage."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class EcrJccDocument(Base):
    __tablename__ = "ecr_jcc_documents"
    __table_args__ = (
        UniqueConstraint("package_id", name="uq_ecr_jcc_package"),
        UniqueConstraint("id", "package_id", name="uq_ecr_jcc_id_package"),
        CheckConstraint("format IN ('PDF', 'IMAGES')", name="jcc_format"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_packages.id", ondelete="RESTRICT")
    )
    format: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )


class EcrPackageAttachment(Base):
    __tablename__ = "ecr_package_attachments"
    __table_args__ = (
        UniqueConstraint(
            "package_id", "kind", "position", name="uq_ecr_attachment_order"
        ),
        UniqueConstraint("storage_key", name="uq_ecr_attachment_storage"),
        ForeignKeyConstraint(
            ["document_id", "package_id"],
            ["ecr_jcc_documents.id", "ecr_jcc_documents.package_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("position > 0 AND byte_size > 0", name="attachment_size_order"),
        CheckConstraint(
            "(kind = 'JCC' AND document_id IS NOT NULL) OR (kind = 'TOWER_PHOTO' AND document_id IS NULL)",
            name="attachment_scope",
        ),
        CheckConstraint(
            "media_type IN ('application/pdf', 'image/jpeg', 'image/png') AND (kind <> 'TOWER_PHOTO' OR media_type <> 'application/pdf')",
            name="attachment_media",
        ),
        CheckConstraint(
            "(media_type = 'application/pdf' AND pdf_page_count > 0) OR (media_type <> 'application/pdf' AND pdf_page_count IS NULL)",
            name="attachment_pdf_pages",
        ),
        Index("ix_ecr_attachment_document_package", "document_id", "package_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_packages.id", ondelete="RESTRICT")
    )
    document_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(40))
    display_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(30))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    pdf_page_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )


class EcrPackageAuditEvent(Base):
    __tablename__ = "ecr_package_audit_events"
    __table_args__ = (
        Index("ix_ecr_package_audit_created", "package_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_packages.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(30))
    field_name: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=True
    )
    new_value: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
