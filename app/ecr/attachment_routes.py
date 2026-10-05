"""Private package attachment UI and content routes; storage stays in services."""

import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse, StreamingResponse

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, PasswordReadyUser
from app.core.templates import render_template
from app.ecr import attachments
from app.ecr.attachment_models import EcrPackageAttachment
from app.ecr.attachment_validation import PHOTO_BYTES, stream_size, validate_stream
from app.storage.attachments import get_attachment_storage, iter_chunks

router = APIRouter(tags=["package attachments"])
ACTIONS = {
    "JCC_ADD",
    "JCC_REPLACE",
    "JCC_REMOVE",
    "JCC_REORDER",
    "TOWER_PHOTO_ADD",
    "TOWER_PHOTO_REPLACE",
    "TOWER_PHOTO_REMOVE",
    "TOWER_PHOTO_REORDER",
}


class DiskMultipartParser(MultiPartParser):
    """Disk-backed files from the first byte, including many small JCC pages.

    Only the current HTTP chunk is buffered, never a list of whole page bytes.
    Multipart field/file count limits remain the existing technical safeguards.
    """

    def on_headers_finished(self):
        super().on_headers_finished()
        if self._current_part.file is not None:
            self._current_part.file.file.rollover()


async def attachment_form(request):
    if (
        request.headers.get("content-type", "").split(";", 1)[0]
        != "multipart/form-data"
    ):
        return await request.form(max_files=1000)
    parser = DiskMultipartParser(request.headers, request.stream(), max_files=1000)
    try:
        return await parser.parse()
    except BaseException as exc:
        # Also close private temporary files on cancellation or disk failure.
        for file in parser._files_to_close_on_error:
            file.close()
        if isinstance(exc, MultiPartException):
            raise HTTPException(400, exc.message) from None
        raise


@router.get("/ecr/packages/{package_id}/attachments", name="package_attachments")
def attachment_page(
    request: Request, package_id: int, db: DatabaseSession, actor: PasswordReadyUser
):
    package = attachments.visible_package(db, package_id, actor)
    return render_template(
        request,
        "ecr/package_attachments.html",
        attachments.page_context(db, package, actor),
    )


@router.post(
    "/ecr/packages/{package_id}/attachments", name="package_attachment_mutation"
)
async def attachment_mutation(
    request: Request, package_id: int, db: DatabaseSession, actor: PasswordReadyUser
):
    # Check header CSRF before multipart parsing; browser always supplies it.
    validate_csrf(
        request, request.headers.get("X-CSRF-Token"), request.app.state.settings
    )
    staged_form = None
    uploads = []
    publication_started = False
    store = get_attachment_storage(request)
    try:
        package = attachments.visible_package(db, package_id, actor)
        if not attachments.editable(db, package, actor):
            raise HTTPException(
                403,
                "Package attachments are read-only: only the package creator owning a report may edit, and no Cell may be Submitted or Approved.",
            )
        # End the preliminary read transaction before receiving/validating files.
        # Final publication must use fresh locking reads, never this snapshot.
        db.rollback()
        staged_form = await attachment_form(request)
        action = staged_form.get("action")
        if action not in ACTIONS:
            raise ValueError("Unknown attachment action.")
        target_id = (
            int(staged_form["target_id"]) if staged_form.get("target_id") else None
        )
        order = json.loads(staged_form["order"]) if staged_form.get("order") else None
        files = staged_form.getlist("files")
        photo_upload = action.startswith("TOWER_PHOTO")
        if photo_upload and len(files) > 5:
            raise ValueError("A package may contain at most 5 Tower Photos.")
        uploaded_bytes = 0
        for file in files:
            if not isinstance(file, UploadFile):
                raise TypeError("Select attachment files.")
            uploaded_bytes += await run_in_threadpool(stream_size, file.file)
            if photo_upload and uploaded_bytes > PHOTO_BYTES:
                raise ValueError("Tower Photos must total no more than 5 MB combined.")
            # Multipart files already live on disk. Decode/stage one at a time.
            validated = await run_in_threadpool(
                validate_stream,
                file.file,
                file.filename,
                allow_pdf=action.startswith("JCC"),
            )
            uploads.append(await run_in_threadpool(attachments.stage, store, validated))
            await file.close()
        publication_started = True
        attachments.publish(
            db,
            package_id,
            actor,
            store,
            action,
            uploads,
            target_id=target_id,
            order=order,
        )
        return JSONResponse(
            {"ok": True, "message": "Package attachments saved."},
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException:
        db.rollback()
        raise
    except (ValueError, TypeError) as exc:
        db.rollback()
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    except (OSError, SQLAlchemyError):
        db.rollback()
        return JSONResponse(
            {
                "ok": False,
                "message": "Unable to save package attachments. Prior attachments are preserved; reload and retry.",
            },
            status_code=503,
        )
    finally:
        if not publication_started:
            attachments.cleanup(store, [u.storage_key for u in uploads])
        if staged_form is not None:
            await staged_form.close()


@router.get(
    "/ecr/packages/{package_id}/attachments/{attachment_id}",
    name="private_package_attachment",
)
def attachment_content(
    request: Request,
    package_id: int,
    attachment_id: int,
    db: DatabaseSession,
    actor: PasswordReadyUser,
    download: bool = False,
):
    attachments.visible_package(db, package_id, actor)
    record = db.scalar(
        select(EcrPackageAttachment).where(
            EcrPackageAttachment.id == attachment_id,
            EcrPackageAttachment.package_id == package_id,
        )
    )
    if record is None:
        raise HTTPException(404, "Attachment not found")
    try:
        content = get_attachment_storage(request).open_reader(record.storage_key)
    except (OSError, ValueError):
        raise HTTPException(404, "Attachment content unavailable") from None
    extension = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}[
        record.media_type
    ]
    return StreamingResponse(
        iter_chunks(content),
        media_type=record.media_type,
        background=BackgroundTask(content.close),
        headers={
            "Content-Length": str(record.byte_size),
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{record.kind.lower()}-{record.id}.{extension}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
