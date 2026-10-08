"""Shared Jinja rendering with consistent CSRF cookie issuance."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth.csrf import get_or_create_csrf_token, set_csrf_cookie
from app.core.static_assets import static_asset_url
from app.ecr.print_features import server_pdf_enabled

TEMPLATE_DIRECTORY = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))


def file_size(byte_size: int) -> str:
    """Decimal KB/MB for presentation only; validation always uses exact bytes."""
    divisor, unit = (1_000_000, "MB") if byte_size >= 1_000_000 else (1000, "KB")
    value = f"{byte_size / divisor:.2f}".rstrip("0").rstrip(".")
    return f"{value} {unit}"


templates.env.filters["file_size"] = file_size
templates.env.globals["static_asset_url"] = static_asset_url
templates.env.globals["server_pdf_enabled"] = server_pdf_enabled


def render_template(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> Response:
    """Render a page and ensure its forms receive a CSRF token."""
    settings = request.app.state.settings
    csrf_token = get_or_create_csrf_token(request, settings)
    template_context = dict(context or {})
    template_context.update(
        {
            "request": request,
            "csrf_token": csrf_token,
            "current_user": template_context.get("current_user"),
        }
    )
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context=template_context,
        status_code=status_code,
    )
    set_csrf_cookie(response, csrf_token, settings)
    return response
