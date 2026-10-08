"""Protected, read-only official Cell report HTML and on-demand PDFs."""

import base64
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from markupsafe import Markup
from PIL import Image
from starlette.responses import HTMLResponse, Response

from app.auth.dependencies import DatabaseSession, PasswordReadyUser
from app.core.templates import templates
from app.ecr.pdf_renderer import PdfRenderError, render_pdf
from app.ecr.print_features import server_pdf_enabled
from app.ecr.print_viewmodel import PrintDataError, build_print_viewmodel
from app.ecr.services import get_admin_visible_report, get_supervisor_report
from app.storage.protected import get_protected_storage
from app.users.models import UserRole

router = APIRouter(prefix="/ecr/reports", tags=["official-print"])
STATIC = Path(__file__).resolve().parents[1] / "static"
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


def official_context(request, db, user, report_id):
    # Reuse the same eager, historical-scoped report lookups as report/sign views.
    report = (
        get_supervisor_report(db, report_id, user.id)
        if user.role is UserRole.SUPERVISOR
        else get_admin_visible_report(db, report_id, user)
    )
    if report is None:
        raise HTTPException(404, "Report not found", headers=PRIVATE_HEADERS)
    try:
        vm = build_print_viewmodel(report)
        signature_uri = ""
        resources = {}

        def image_uri(content):
            encoded = base64.b64encode(content).decode("ascii")
            uri = f"data:image/png;base64,{encoded}"
            resources[uri] = encoded
            return uri

        logo_uri = image_uri((STATIC / "brand" / "paharpur-logo.png").read_bytes())
        if report.page3 and report.page3.customer_signature_storage_key:
            # Authorization and historical identity checks completed BEFORE read.
            content = get_protected_storage(request).read(
                report.page3.customer_signature_storage_key
            )
            # Missing/corrupt evidence must not silently disappear in WeasyPrint.
            with Image.open(BytesIO(content)) as sign:
                if (
                    sign.format != "PNG"
                    or sign.width > 2048
                    or sign.height > 1024
                    or sign.width * sign.height > 2_000_000
                    or getattr(sign, "n_frames", 1) != 1
                ):
                    raise ValueError("Invalid saved Customer Sign")
                sign.load()
            signature_uri = image_uri(content)
        return {
            "request": request,
            "vm": vm,
            "logo_uri": logo_uri,
            "signature_uri": signature_uri,
            "resources": resources,
            "back_url": f"/ecr/reports/{report.id}"
            if user.role is UserRole.SUPERVISOR
            else f"/reports/{report.id}",
        }
    except PrintDataError as exc:
        raise HTTPException(409, str(exc), headers=PRIVATE_HEADERS) from exc
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            503,
            "Official report evidence/branding is unavailable. No report was generated.",
            headers=PRIVATE_HEADERS,
        ) from exc


@router.get("/{report_id}/print", response_class=HTMLResponse)
def print_report(
    request: Request, report_id: int, db: DatabaseSession, user: PasswordReadyUser
):
    context = official_context(request, db, user, report_id)
    return templates.TemplateResponse(
        request=request,
        name="ecr/official_report.html",
        context={**context, "pdf": False},
        headers=PRIVATE_HEADERS,
    )


@router.get("/{report_id}/pdf")
def download_pdf(
    request: Request, report_id: int, db: DatabaseSession, user: PasswordReadyUser
):
    # Gate before report/evidence access, renderer slot acquisition or subprocess work.
    if not server_pdf_enabled():
        raise HTTPException(404, "Not found", headers=PRIVATE_HEADERS)
    # Sync route runs off the event loop; renderer concurrency is independently bounded.
    context = official_context(request, db, user, report_id)
    vm = context["vm"]
    html = templates.env.get_template("ecr/official_report.html").render(
        **context, pdf=True, print_css=Markup((STATIC / "ecr_print.css").read_text())
    )
    try:
        content = render_pdf(html, context["resources"], 3 + len(vm.continuations))
    except PdfRenderError as exc:
        raise HTTPException(503, str(exc), headers=PRIVATE_HEADERS) from exc
    return Response(
        content,
        media_type="application/pdf",
        headers={
            **PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="{vm.filename}"',
        },
    )
