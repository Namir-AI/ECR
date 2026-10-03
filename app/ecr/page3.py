"""Page 3 partial Draft text and strict drawing-only PNG validation."""

import base64
import binascii
from io import BytesIO

from fastapi import Request
from PIL import Image, ImageChops, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.ecr.models import EcrPage3, EcrReport

TEXT_LIMIT = 100_000  # Technical request/storage safety limit, not an ECR rule.
PNG_LIMIT = 2 * 1024 * 1024
JSON_LIMIT = 3 * 1024 * 1024


class Page3DraftInput(BaseModel):
    team_leader_report: str | None = Field(
        default=None, max_length=TEXT_LIMIT, json_schema_extra={"final_required": True}
    )
    customer_comment: str | None = Field(
        default=None, max_length=TEXT_LIMIT, json_schema_extra={"final_required": False}
    )

    @field_validator("*", mode="before")
    @classmethod
    def empty_text(cls, value):
        # Preserve meaningful whitespace/newlines; whitespace-only text is absent.
        if isinstance(value, str):
            value = value.replace("\r\n", "\n").replace("\r", "\n")
            return value if value.strip() else None
        return value


class SignatureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signature_png: str = Field(max_length=JSON_LIMIT)


async def form_snapshot(request: Request) -> dict | None:
    form = await request.form()
    if form.get("page3_present") != "1":
        return None
    return {name: form.get(name, "") for name in Page3DraftInput.model_fields}


def ensure_page3(db: Session, report: EcrReport) -> EcrPage3:
    if report.page3 is None:
        report.page3 = EcrPage3(report=report)
        db.add(report.page3)
    return report.page3


def save_text(db: Session, report: EcrReport, data: Page3DraftInput) -> None:
    record = ensure_page3(db, report)
    for name, value in data.model_dump().items():
        setattr(record, name, value)
    report.updated_at = utc_now()


def values(report: EcrReport) -> dict:
    return {
        name: (getattr(report.page3, name) or "") if report.page3 else ""
        for name in Page3DraftInput.model_fields
    }


def validate_signature(data_url: str) -> bytes:
    """Fully decode bounded PNG, inspect ink, re-encode to strip untrusted metadata."""
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("Save a drawing from the signature pad (PNG only).")
    try:
        raw = base64.b64decode(data_url[len(prefix) :], validate=True)
        if not raw or len(raw) > PNG_LIMIT:
            raise ValueError("Signature exceeds the 2 MiB technical limit.")
        with Image.open(BytesIO(raw), formats=["PNG"]) as image:
            width, height = image.size
            if (
                width > 2048
                or height > 1024
                or width * height > 2_000_000
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("Signature drawing dimensions are too large.")
            image.load()  # Do not merely check a header or client-supplied flag.
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", image.size, "white")
            flattened = Image.alpha_composite(white, rgba).convert("RGB")
            difference = ImageChops.difference(
                flattened, Image.new("RGB", image.size, "white")
            )
            if difference.getbbox() is None:
                raise ValueError("Draw a customer signature before saving.")
            output = BytesIO()
            flattened.save(output, format="PNG")
            return output.getvalue()
    except (
        binascii.Error,
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError("The signature drawing is not a valid PNG.") from exc
