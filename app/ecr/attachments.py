"""Package-authorized attachment transactions and safe post-commit cleanup."""

import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select

from app.ecr.attachment_models import (
    EcrJccDocument,
    EcrPackageAttachment,
    EcrPackageAuditEvent,
)
from app.ecr.attachment_validation import PHOTO_BYTES, PHOTO_COUNT
from app.ecr.models import EcrPackage, EcrReport, EcrReportStatus, EcrTower
from app.users.models import UserRole


def visible_package(db, package_id, actor, *, lock=False):
    query = select(EcrPackage).where(EcrPackage.id == package_id)
    if actor.role is not UserRole.SUPERADMIN:
        reports = (
            select(EcrReport.id).join(EcrTower).where(EcrTower.package_id == package_id)
        )
        if actor.role is UserRole.SUPERVISOR:
            reports = reports.where(EcrReport.supervisor_user_id == actor.id)
        else:
            reports = reports.where(EcrReport.branch_id == actor.branch_id)
        query = query.where(reports.exists())
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    package = db.scalar(query)
    if package is None:
        raise HTTPException(404, "Package not found")
    return package


def editable(db, package, actor, *, lock=False):
    if actor.role is not UserRole.SUPERVISOR or package.created_by_user_id != actor.id:
        return False
    query = (
        select(EcrReport.supervisor_user_id, EcrReport.status)
        .join(EcrTower)
        .where(EcrTower.package_id == package.id)
    )
    if lock:
        # Current read after package lock, not an old REPEATABLE READ snapshot.
        query = query.with_for_update()
    rows = list(db.execute(query))
    return any(owner == actor.id for owner, _ in rows) and not any(
        status in (EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED)
        for _, status in rows
    )


def file_records(db, package_id, *, lock=False):
    query = (
        select(EcrPackageAttachment)
        .where(EcrPackageAttachment.package_id == package_id)
        .order_by(EcrPackageAttachment.kind, EcrPackageAttachment.position)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(db.scalars(query))


def display_metadata(records):
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "position": r.position,
            "filename": r.display_filename,
            "media_type": r.media_type,
            "byte_size": r.byte_size,
            "pdf_page_count": r.pdf_page_count,
        }
        for r in records
    ]


def page_context(db, package, actor):
    records = file_records(db, package.id)
    photos = [r for r in records if r.kind == "TOWER_PHOTO"]
    return {
        "package": package,
        "current_user": actor,
        "editable": editable(db, package, actor),
        "jcc": [r for r in records if r.kind == "JCC"],
        "photos": photos,
        "photo_bytes": sum(r.byte_size for r in photos),
        "photo_limit": PHOTO_BYTES,
    }


def cleanup(store, keys):
    for key in keys:
        try:
            store.delete(key)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning(
                "Private package attachment cleanup deferred"
            )


@dataclass(frozen=True)
class StagedAttachment:
    storage_key: str
    filename: str
    media_type: str
    byte_size: int
    pdf_page_count: int | None


def stage(store, upload):
    """Copy one validated seekable file before acquiring business locks."""
    try:
        return StagedAttachment(
            store.put_stream(upload.stream, upload.media_type),
            upload.filename,
            upload.media_type,
            upload.byte_size,
            upload.pdf_page_count,
        )
    finally:
        upload.close()


def publish(db, package_id, actor, store, action, uploads, **options):
    """Fresh current reads serialize final publication with Cell submission."""
    try:
        package = visible_package(db, package_id, actor, lock=True)
        if not editable(db, package, actor, lock=True):
            raise HTTPException(
                403,
                "Package attachments are read-only: only the package creator owning a report may edit, and no Cell may be Submitted or Approved.",
            )
    except Exception:
        try:
            db.rollback()
        finally:
            cleanup(store, [u.storage_key for u in uploads])
        raise
    # mutate owns cleanup from here, including ambiguous commit acknowledgement.
    mutate(db, package, actor, store, action, uploads, **options)


def resequence(db, records):
    # Avoid transient unique-key conflicts when swapping/deleting positions.
    offset = max((r.position for r in records), default=0) + len(records) + 1
    for index, record in enumerate(records, 1):
        record.position = offset + index
    db.flush()
    for index, record in enumerate(records, 1):
        record.position = index
    db.flush()


def mutate(db, package, actor, store, action, uploads, *, target_id=None, order=None):
    """Caller holds package/status locks. Commit references/audit before unlink.

    Newly staged objects are removed on failure; prior referenced objects remain.
    Cleanup failures leave private orphans for reviewed future reconciliation.
    """
    staged = [u.storage_key for u in uploads if isinstance(u, StagedAttachment)]
    obsolete = []
    commit_started = False
    try:
        records = file_records(db, package.id, lock=True)
        before = display_metadata(records)
        kind = "JCC" if action.startswith("JCC_") else "TOWER_PHOTO"
        selected = [r for r in records if r.kind == kind]
        document = db.scalar(
            select(EcrJccDocument)
            .where(EcrJccDocument.package_id == package.id)
            .with_for_update()
        )
        if action.endswith("REORDER"):
            if (
                uploads
                or not isinstance(order, list)
                or any(type(n) is not int for n in order)
                or len(order) != len(selected)
                or set(order) != {r.id for r in selected}
            ):
                raise ValueError(
                    "Order must include every current attachment exactly once. Reload and retry."
                )
            by_id = {r.id: r for r in selected}
            resequence(db, [by_id[n] for n in order])
        elif action.endswith("REMOVE"):
            if uploads:
                raise ValueError("Removal must not include uploaded files.")
            removed = (
                selected
                if action == "JCC_REMOVE" and target_id is None
                else [r for r in selected if r.id == target_id]
            )
            if not removed:
                raise ValueError("Attachment no longer exists. Reload and retry.")
            for record in removed:
                obsolete.append(record.storage_key)
                db.delete(record)
            db.flush()
            remaining = [r for r in selected if r not in removed]
            resequence(db, remaining)
            if kind == "JCC" and not remaining:
                db.delete(document)
        else:
            if not uploads:
                raise ValueError("Choose attachment files before saving.")
            removed = []
            if action == "JCC_REPLACE":
                removed = selected
            elif action == "TOWER_PHOTO_REPLACE":
                removed = [r for r in selected if r.id == target_id]
                if len(removed) != 1 or len(uploads) != 1:
                    raise ValueError("Select one current photo and one replacement.")
            if action == "JCC_ADD" and document and document.format == "PDF":
                raise ValueError(
                    "Replace the current PDF to change JCC; images cannot be added to a PDF JCC."
                )
            if kind == "JCC":
                pdf = any(u.media_type == "application/pdf" for u in uploads)
                if pdf and (
                    len(uploads) != 1 or (selected and action != "JCC_REPLACE")
                ):
                    raise ValueError(
                        "Use one PDF or ordered images for one logical JCC, not a mixture."
                    )
                if document is None:
                    document = EcrJccDocument(
                        package_id=package.id,
                        format="PDF" if pdf else "IMAGES",
                        created_by_user_id=actor.id,
                    )
                    db.add(document)
                    db.flush()
                elif action == "JCC_REPLACE":
                    document.format = "PDF" if pdf else "IMAGES"
            else:
                if any(u.media_type == "application/pdf" for u in uploads):
                    raise ValueError("Tower Photos must be JPG/JPEG or PNG.")
                kept = [r for r in selected if r not in removed]
                if len(kept) + len(uploads) > PHOTO_COUNT:
                    raise ValueError("A package may contain at most 5 Tower Photos.")
                if (
                    sum(r.byte_size for r in kept) + sum(u.byte_size for u in uploads)
                    > PHOTO_BYTES
                ):
                    raise ValueError(
                        "Tower Photos must total no more than 5 MB combined."
                    )
            replacement_position = (
                removed[0].position if action == "TOWER_PHOTO_REPLACE" else None
            )
            for record in removed:
                obsolete.append(record.storage_key)
                db.delete(record)
            db.flush()
            kept = [r for r in selected if r not in removed]
            next_position = max((r.position for r in kept), default=0) + 1
            for index, upload in enumerate(uploads):
                db.add(
                    EcrPackageAttachment(
                        package_id=package.id,
                        document_id=document.id if kind == "JCC" else None,
                        kind=kind,
                        position=replacement_position or next_position + index,
                        storage_key=upload.storage_key,
                        display_filename=upload.filename,
                        media_type=upload.media_type,
                        byte_size=upload.byte_size,
                        pdf_page_count=upload.pdf_page_count,
                        created_by_user_id=actor.id,
                    )
                )
        db.flush()
        after = display_metadata(file_records(db, package.id, lock=True))
        audit_action = (
            "JCC_ADD"
            if action == "JCC_REPLACE" and not any(r["kind"] == "JCC" for r in before)
            else action
        )
        db.add(
            EcrPackageAuditEvent(
                package_id=package.id,
                actor_user_id=actor.id,
                actor_role=actor.role.value,
                action=audit_action,
                field_name=kind,
                old_value=json.dumps(before),
                new_value=json.dumps(after),
            )
        )
        commit_started = True
        db.commit()
    except Exception:
        # A lost commit acknowledgement is ambiguous. Keep staged objects rather
        # than risk deleting a now-committed reference; reconcile later.
        try:
            db.rollback()
        finally:
            if not commit_started:
                cleanup(store, staged)
        raise
    cleanup(store, obsolete)
