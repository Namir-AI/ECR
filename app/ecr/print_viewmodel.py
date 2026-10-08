"""Official Letter report presentation, independent of HTML and PDF rendering.

Print capacities are presentation rules, never data/engineering limits. Wrapped
lines retain every character; overflow goes to separately numbered sheets.
"""

import re
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal
from functools import lru_cache
from typing import ClassVar
from zoneinfo import ZoneInfo

from PIL import ImageFont

from app.ecr import page1, page2
from app.ecr.models import EcrReportStatus
from app.ecr.series import (
    CONDITIONAL_SECTIONS,
    COOLING_TOWER_SERIES,
    applicable_sections,
)

CORE_BLADE_CAPACITY = 8
CORE_TORQUE_CAPACITY = 4
TEAM_CORE_LINES = 28
COMMENT_CORE_LINES = 6
CONTINUATION_LINES = 44  # Reserve header space for maximum-width serial identities.
TORQUE_ORDER = (
    ("TOWER", "Tower Fasteners"),
    ("MECH_HOLD_DOWN", "Mechanical Equipment Hold Down Fasteners"),
    ("FAN_HARDWARE", "Fan Hardware Fastener"),
)


class PrintDataError(ValueError):
    """An official report cannot be rendered without authoritative identity/evidence."""


@lru_cache
def print_font():
    # Installed open font, not a bundled proprietary asset. 12 px = 9 pt.
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 12)
    except OSError as exc:
        raise PrintDataError(
            "Official printing requires the installed DejaVu Sans font."
        ) from exc


def wrapped_lines(text: str, width_pt: float) -> tuple[str, ...]:
    """Conservative glyph-width wrapping, including long unbroken values/newlines.

    A 12% width reserve absorbs kerning/shaping differences across Pillow, browser
    and Pango. No characters/whitespace are removed, including blank paragraphs.
    """
    font = print_font()
    advances = {}
    capacity = width_pt * 4 / 3 * 0.88
    result = []
    for paragraph in text.expandtabs(4).split("\n"):
        line, width = "", 0.0
        for char in paragraph:
            if char not in advances:
                advances[char] = font.getlength(char)
            advance = advances[char]
            if line and width + advance > capacity:
                boundary = line.rfind(" ") + 1
                if boundary:
                    result.append(line[:boundary])
                    line = line[boundary:]
                    width = sum(advances[c] for c in line)
                else:
                    result.append(line)
                    line, width = "", 0.0
                if line and width + advance > capacity:
                    result.append(line)
                    line, width = "", 0.0
            line += char
            width += advance
        result.append(line)
    return tuple(result)


def value_text(value, unit="") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    value = page1.display_value(value)
    return value + (unit if unit == "°" else f" {unit}" if unit else "")


def browser_file_title(text: str) -> str:
    """Conservative filename suggestion only; never a stored value or server path."""
    return " ".join(
        "".join(c if c.isalnum() or c in " -_()" else " " for c in text).split()
    )


@dataclass(frozen=True)
class Field:
    label: str
    value: str


@dataclass(frozen=True)
class Section:
    name: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Continuation:
    section: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Alignment:
    end: str
    top: str
    right: str
    bottom: str
    left: str
    shift_x: str
    shift_y: str


@dataclass(frozen=True)
class OfficialReport:
    blade_capacity: ClassVar[int] = CORE_BLADE_CAPACITY
    torque_capacity: ClassVar[int] = CORE_TORQUE_CAPACITY
    team_capacity: ClassVar[int] = TEAM_CORE_LINES
    comment_capacity: ClassVar[int] = COMMENT_CORE_LINES
    report_id: int
    serial: str
    cell: int
    filename: str
    browser_document_title: str
    status: str
    status_label: str
    identity: tuple[Field, ...]
    page1_sections: tuple[Section, ...]
    page2_sections: tuple[Section, ...]
    blades: tuple[str, ...]
    blades_overflow: bool
    torque: tuple[tuple[str, tuple[tuple[str, str], ...], bool], ...]
    radial: str
    axial: str
    alignment: tuple[Alignment, ...]
    alignment_unit: str
    switches: tuple[Field, ...]
    supervisor_name: str
    supervisor_block_name: str
    employee_id: str
    team_lines: tuple[str, ...]
    comment_lines: tuple[str, ...]
    sign_date: str
    continuations: tuple[Continuation, ...]


def alignment(record, end: str) -> Alignment:
    values = {
        side: getattr(record, f"{end}_{side}", None)
        for side in ("top", "right", "bottom", "left")
    }

    def shift(a, b):
        # An incomplete pair has no projection. Explicit zero remains a reading.
        if values[a] is None or values[b] is None:
            return "0"
        reading = (values[a] - values[b]) / 2
        return value_text(18 * min(Decimal(1), max(Decimal(-1), reading)))

    return Alignment(
        end.upper(),
        *(
            "" if values[s] is None else f"{values[s]:.2f}"
            for s in ("top", "right", "bottom", "left")
        ),
        shift("right", "left"),
        shift("top", "bottom"),
    )


def build_print_viewmodel(report) -> OfficialReport:
    package, tower = report.tower.package, report.tower
    if package.multiple_towers and not tower.tower_suffix:
        raise PrintDataError("Multi-tower report has no authoritative Tower suffix.")
    serial = (
        tower.display_name
        if package.multiple_towers
        else package.cooling_tower_serial_no
    )
    historical = report.status in (EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED)
    if historical:
        name, employee = (
            report.supervisor_name_snapshot,
            report.supervisor_employee_id_snapshot,
        )
        if not name or not employee:
            raise PrintDataError(
                "Submitted/Approved report is missing its Supervisor identity snapshot."
            )
    else:
        name, employee = report.supervisor.full_name, report.supervisor.employee_id
    continuations = []

    def continue_text(section, text):
        lines = wrapped_lines(text, 510)
        for start in range(0, len(lines), CONTINUATION_LINES):
            continuations.append(
                Continuation(section, lines[start : start + CONTINUATION_LINES])
            )

    def fit(
        label,
        text,
        width=112,
        limit=1,
        section="IDENTIFICATION",
        notice="See Continuation",
    ):
        if len(wrapped_lines(text, width)) > limit:
            continue_text(f"{section} — {label}", text)
            return notice
        return text

    def sections(metadata, record):
        result = []
        applicable = applicable_sections(package.cooling_tower_series)
        for section, fields in metadata:
            inactive = section in CONDITIONAL_SECTIONS and section not in applicable
            known = package.cooling_tower_series in COOLING_TOWER_SERIES
            items = []
            for field, label, kind, extra in fields:
                if field == "drive_shaft_oal_unit":
                    continue
                label = {
                    "motor_serial_no": "Motor Serial No.",
                    "drive_shaft_oal": "OAL",
                    "general_tower_hardware": "General / Tower Hardware",
                }.get(field, label)
                unit = extra if kind == "number" else ""
                if field == "drive_shaft_oal":
                    unit = getattr(record, "drive_shaft_oal_unit", None) or ""
                text = (
                    ("N/A" if known else "")
                    if inactive
                    else value_text(getattr(record, field, None), unit)
                )
                items.append(Field(label, fit(label, text, section=section.upper())))
            result.append(Section(section, tuple(items)))
        return tuple(result)

    identity = []
    for label, value, width in (
        ("Customer Name", package.customer, 360),
        ("Customer Order No.", package.customer_order_no, 360),
        ("Paharpur Sl.No.", serial, 240),
        ("Cooling Tower Series", package.cooling_tower_series, 110),
        ("Model", package.model, 110),
        ("No. of Cells", tower.declared_no_of_cells, 70),
        ("Cell No.", report.cell_no, 70),
        ("Place of Installation", package.place_of_installation, 360),
        (
            "Erection Start Date",
            report.erection_start_date.strftime("%d-%m-%Y")
            if report.erection_start_date
            else "",
            110,
        ),
        (
            "Erection Completion Date",
            report.erection_completion_date.strftime("%d-%m-%Y")
            if report.erection_completion_date
            else "",
            110,
        ),
    ):
        text = value_text(value)
        identity.append(Field(label, fit(label, text, width, 2)))
    first_sections = sections(page1.SECTIONS, report.page1)
    second_sections = sections(page2.SECTIONS, report.page2)

    blades = tuple(
        b.serial_no
        for b in sorted(
            report.page1.blade_serials if report.page1 else [], key=lambda b: b.position
        )
    )
    core_blades = tuple(
        fit(f"Blade {i + 1}", b, 45, section="FAN BLADE SERIALS", notice="See Cont.")
        for i, b in enumerate(blades[:CORE_BLADE_CAPACITY])
    )
    if len(blades) > CORE_BLADE_CAPACITY:
        continue_text(
            "FAN BLADE SERIALS — CONTINUATION",
            "\n".join(
                f"{i}: {b}"
                for i, b in enumerate(
                    blades[CORE_BLADE_CAPACITY:], CORE_BLADE_CAPACITY + 1
                )
            ),
        )

    torque = []
    for category, label in TORQUE_ORDER:
        rows = sorted(
            (r for r in report.fastener_rows if r.category == category),
            key=lambda r: r.sequence_no,
        )
        core = tuple(
            (
                fit(
                    f"Row {r.sequence_no} — Diameter",
                    value_text(r.diameter),
                    64,
                    section=label,
                ),
                fit(
                    f"Row {r.sequence_no} — Torque",
                    value_text(r.torque, r.torque_unit or ""),
                    75,
                    section=label,
                ),
            )
            for r in rows[:CORE_TORQUE_CAPACITY]
        )
        torque.append((label, core, len(rows) > CORE_TORQUE_CAPACITY))
        if len(rows) > CORE_TORQUE_CAPACITY:
            continue_text(
                f"FASTENER TORQUE DETAILS — {label.upper()} — CONTINUATION",
                "\n".join(
                    f"Row {r.sequence_no}: Diameter: {value_text(r.diameter)}; Torque: {value_text(r.torque, r.torque_unit or '')}"
                    for r in rows[CORE_TORQUE_CAPACITY:]
                ),
            )

    def narrative(field, label, width, capacity):
        text = getattr(report.page3, field, None) or ""
        lines = wrapped_lines(text, width)
        if len(lines) > capacity:
            continue_text(f"{label} — CONTINUATION", text)
            return ("See Continuation",)
        return lines

    team = narrative("team_leader_report", "DETAILED REPORT", 510, TEAM_CORE_LINES)
    comment = narrative("customer_comment", "CUSTOMER COMMENT", 235, COMMENT_CORE_LINES)
    record = report.page3
    sign_date = ""
    if record and record.customer_signature_storage_key:
        if not record.customer_signed_at:
            raise PrintDataError(
                "Customer Sign is missing its authoritative timestamp."
            )
        signed = record.customer_signed_at
        if signed.tzinfo is None:
            signed = signed.replace(tzinfo=UTC)
        sign_date = signed.astimezone(ZoneInfo("Asia/Kolkata")).strftime(
            "%d-%m-%Y %H:%M IST"
        )
    components = [package.cooling_tower_serial_no]
    if package.multiple_towers:
        components.append(tower.tower_suffix)
    slug = "_".join(re.sub(r"[^A-Za-z0-9_-]", "_", c) for c in components)
    status = report.status.value
    display_name = fit("Erected by", name, 235, 2, "SUPERVISOR IDENTITY")
    block_name = fit(
        "Name (in Block Letters)", name.upper(), 235, 2, "SUPERVISOR IDENTITY"
    )
    employee = fit("Employee ID", employee, 180, 2, "SUPERVISOR IDENTITY")
    return OfficialReport(
        report.id,
        serial,
        report.cell_no,
        f"ECR_{slug}_Cell-{report.cell_no}.pdf",
        browser_file_title(f"{serial} {package.place_of_installation or ''} ECR"),
        status,
        f"{status} — NOT SUBMITTED" if not historical else status,
        tuple(identity),
        first_sections,
        second_sections,
        core_blades,
        len(blades) > CORE_BLADE_CAPACITY,
        tuple(torque),
        value_text(getattr(report.page2, "radial_tir", None)),
        value_text(getattr(report.page2, "axial_tir", None)),
        tuple(alignment(report.page2, end) for end in ("de", "nde")),
        getattr(report.page2, "de_nde_unit", None) or "",
        tuple(
            Field(label, value_text(getattr(report.page2, field, None)))
            for field, label in (
                ("vibration_limit_switch", "Vibration Limit Switch"),
                ("oil_level_switch", "Oil Level Switch"),
            )
        ),
        display_name,
        block_name,
        employee,
        team,
        comment,
        sign_date,
        tuple(continuations),
    )
