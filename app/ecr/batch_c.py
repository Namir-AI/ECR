"""Batch C capture; same report lock, transaction and autosave as Pages 1/2.

Separate form marker preserves Batch C when a previously opened A+B page saves.
Final completeness is metadata only; incomplete Drafts remain saveable.
"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.ecr.models import EcrFastenerTorqueRow, EcrPage2Technical, EcrReport
from app.ecr.page1 import Number, Text, display_value, draft_field
from app.ecr.page2 import YesNo

CATEGORIES = (
    ("FAN_HARDWARE", "Fan Hardware Fastener"),
    ("TOWER", "Tower Fasteners"),
    ("MECH_HOLD_DOWN", "Mechanical Equipment Hold Down Fasteners"),
)
POSITIONS = ("top", "right", "bottom", "left")
FINAL_REQUIRED_CATEGORIES = frozenset({"FAN_HARDWARE"})
Tir = Annotated[Decimal, Field(ge=-1, le=1, decimal_places=3)]
Reading = Annotated[Decimal, Field(ge=-1, le=1, decimal_places=2)]


class BlankInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def blanks(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value


class TorqueRowInput(BlankInput):
    category: Literal["FAN_HARDWARE", "TOWER", "MECH_HOLD_DOWN"]
    diameter: Text | None = draft_field(final_required=True)
    torque: Number | None = draft_field(final_required=True)
    torque_unit: Literal["ft-lbs", "Nm"] | None = draft_field(final_required=True)


class BatchCDraftInput(BlankInput):
    radial_tir: Tir | None = draft_field(final_required=False)
    axial_tir: Tir | None = draft_field(final_required=False)
    de_nde_unit: Literal["inches", "mm"] | None = draft_field(final_required=True)
    de_top: Reading | None = draft_field(final_required=True)
    de_right: Reading | None = draft_field(final_required=True)
    de_bottom: Reading | None = draft_field(final_required=True)
    de_left: Reading | None = draft_field(final_required=True)
    nde_top: Reading | None = draft_field(final_required=True)
    nde_right: Reading | None = draft_field(final_required=True)
    nde_bottom: Reading | None = draft_field(final_required=True)
    nde_left: Reading | None = draft_field(final_required=True)
    vibration_limit_switch: YesNo | None = draft_field(final_required=True)
    oil_level_switch: YesNo | None = draft_field(final_required=True)
    fastener_rows: list[TorqueRowInput] = Field(default_factory=list)


SCALAR_FIELDS = tuple(n for n in BatchCDraftInput.model_fields if n != "fastener_rows")
SWITCH_FIELDS = ("vibration_limit_switch", "oil_level_switch")
FINAL_REQUIRED_FIELDS = frozenset(
    n
    for n in SCALAR_FIELDS
    if BatchCDraftInput.model_fields[n].json_schema_extra["final_required"]
)


async def form_snapshot(request: Request) -> dict | None:
    form = await request.form()
    if form.get("batch_c_present") != "1":
        return None
    # Each category has aligned repeated form columns; mismatches are rejected
    # rather than truncating a partially/crafted submitted row.
    rows = []
    for category, _ in CATEGORIES:
        columns = {
            key: form.getlist(f"torque_{category}_{key}")
            for key in ("diameter", "torque", "torque_unit")
        }
        lengths = {len(v) for v in columns.values()}
        if len(lengths) != 1:
            rows.append({"category": "INVALID"})
            continue
        for index in range(len(columns["diameter"])):
            rows.append(
                {"category": category, **{k: v[index] for k, v in columns.items()}}
            )
    return {**{n: form.get(n, "") for n in SCALAR_FIELDS}, "fastener_rows": rows}


def values(report: EcrReport) -> dict:
    result = {n: display_value(getattr(report.page2, n, None)) for n in SCALAR_FIELDS}
    for n in SWITCH_FIELDS:
        value = getattr(report.page2, n, None)
        result[n] = "" if value is None else ("Yes" if value else "No")
    result["fastener_rows"] = [
        {
            "category": row.category,
            "diameter": display_value(row.diameter),
            "torque": display_value(row.torque),
            "torque_unit": display_value(row.torque_unit),
        }
        for category, _ in CATEGORIES
        for row in report.fastener_rows
        if row.category == category
    ]
    return result


def save(db: Session, report: EcrReport, data: BatchCDraftInput) -> None:
    if report.page2 is None:
        report.page2 = EcrPage2Technical(report=report)
        db.add(report.page2)
    for name in SCALAR_FIELDS:
        value = getattr(data, name)
        if name in SWITCH_FIELDS and value is not None:
            value = value == "Yes"
        setattr(report.page2, name, value)
    # Full ordered snapshot replacement under the existing report/package lock:
    # retrying cannot append duplicates; flush deletes before unique order inserts.
    report.fastener_rows.clear()
    db.flush()
    for category, _ in CATEGORIES:
        sequence = 0
        for row in data.fastener_rows:
            if row.category != category or all(
                getattr(row, n) is None for n in ("diameter", "torque", "torque_unit")
            ):
                continue
            sequence += 1
            report.fastener_rows.append(
                EcrFastenerTorqueRow(sequence_no=sequence, **row.model_dump())
            )
    report.updated_at = utc_now()
