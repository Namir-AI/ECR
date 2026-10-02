"""Frozen Page 1 capture schema, presentation metadata and transactional updates."""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.ecr.models import EcrFanBladeSerial, EcrPage1Technical, EcrReport

Text = Annotated[str, Field(max_length=250)]
Number = Annotated[Decimal, Field(max_digits=38, decimal_places=18)]


def draft_field(*, final_required: bool):
    """Final completeness metadata only; never require a value to save a Draft."""
    return Field(default=None, json_schema_extra={"final_required": final_required})


class Page1DraftInput(BaseModel):
    """Owner-approved final requiredness; every field may remain blank in Draft."""

    motor_make: Text | None = draft_field(final_required=True)
    motor_serial_no: Text | None = draft_field(final_required=True)
    motor_hp: Number | None = draft_field(final_required=True)
    motor_frame: Text | None = draft_field(final_required=False)
    motor_insulation: Text | None = draft_field(final_required=False)
    motor_mounting: Literal["Foot", "Flange"] | None = draft_field(final_required=False)
    motor_speed: Literal["Single", "Twin"] | None = draft_field(final_required=True)
    motor_rpm: Number | None = draft_field(final_required=True)
    motor_full_load_current: Number | None = draft_field(final_required=True)
    motor_current_drawn: Number | None = draft_field(final_required=True)
    fan_serial_no: Text | None = draft_field(final_required=True)
    fan_diameter_type: Text | None = draft_field(final_required=True)
    fan_hardware: Literal["HDG", "SS304", "SS316"] | None = draft_field(
        final_required=True
    )
    fan_no_of_blades: Annotated[int, Field(gt=0)] | None = draft_field(
        final_required=True
    )
    fan_pitch_angle: Number | None = draft_field(final_required=True)
    fan_hub_cover: Literal["Yes", "No"] | None = draft_field(final_required=True)
    fan_cylinder_height: Number | None = draft_field(final_required=True)
    fan_cylinder_material: Literal["FRP", "Plywood"] | None = draft_field(
        final_required=True
    )
    blade_tip_clearance: Number | None = draft_field(final_required=True)
    blade_tip_track_variation: Number | None = draft_field(final_required=True)
    drive_shaft_series: Literal["6Q", "175"] | None = draft_field(final_required=True)
    drive_shaft_class: Literal["CL I", "CL II", "CL III"] | None = draft_field(
        final_required=True
    )
    drive_shaft_oal: Number | None = draft_field(final_required=False)
    drive_shaft_oal_unit: Literal["cm", "inches"] | None = draft_field(
        final_required=False
    )
    drive_shaft_serial_no: Text | None = draft_field(final_required=True)
    # These remain text until the owner supplies the complete lookup lists.
    gearbox_series: Text | None = draft_field(final_required=True)
    gearbox_ratio: Text | None = draft_field(final_required=True)
    gearbox_serial_no: Text | None = draft_field(final_required=True)
    gearbox_model_no: Text | None = draft_field(final_required=False)
    fill_type: Text | None = draft_field(final_required=False)
    fill_material: Literal["PVC", "Timber"] | None = draft_field(final_required=True)
    blade_serials: list[Text] = Field(
        default_factory=list, json_schema_extra={"final_required": False}
    )

    @field_validator("*", mode="before")
    @classmethod
    def strip_blanks(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("blade_serials", mode="before")
    @classmethod
    def remove_empty_blades(cls, value):
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("Blade Serial Nos. must be text entries.")
        return [item.strip() for item in value if item.strip()]

    @field_validator("fan_no_of_blades")
    @classmethod
    def fit_integer_storage(cls, value):
        # MySQL BIGINT capacity, not an engineering/blade-count acceptance limit.
        if value is not None and value > 2**63 - 1:
            raise ValueError("No. of Blades exceeds integer storage capacity.")
        return value

    @field_validator("motor_full_load_current")
    @classmethod
    def positive_full_load_current(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("Full Load Current must be greater than 0 Amps.")
        return value

    @field_validator("motor_hp")
    @classmethod
    def nonnegative_hp(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("HP must be 0 or greater.")
        return value

    @field_validator("motor_current_drawn")
    @classmethod
    def nonnegative_current_drawn(cls, value: Decimal | None) -> Decimal | None:
        # Zero is meaningful data, including when commissioning cannot be completed.
        if value is not None and value < 0:
            raise ValueError("Current Drawn must be 0 Amps or greater.")
        return value

    @model_validator(mode="after")
    def check_oal(self):
        if self.drive_shaft_oal is not None and self.drive_shaft_oal_unit is None:
            raise ValueError("OAL requires a unit: select cm or inches.")
        if self.drive_shaft_oal is None and self.drive_shaft_oal_unit is not None:
            raise ValueError(
                "OAL Unit requires an OAL value; leave both blank if not entered."
            )
        return self


# One source for UI indicators and later completed-report validation (not yet implemented).
FINAL_REQUIRED_FIELDS = frozenset(
    name
    for name, field in Page1DraftInput.model_fields.items()
    if field.json_schema_extra["final_required"]
)


# UI metadata only. Persistence remains dedicated relational columns, not key/value rows.
# field name, label, control type, approved options/unit
SECTIONS = (
    (
        "Motor",
        (
            ("motor_make", "Make", "text", ""),
            ("motor_serial_no", "Motor Serial No. (digital addition)", "text", ""),
            ("motor_hp", "HP", "number", "HP"),
            ("motor_frame", "Frame", "text", ""),
            ("motor_insulation", "Insulation", "text", ""),
            ("motor_mounting", "Mounting", "select", ("Foot", "Flange")),
            ("motor_speed", "Speed", "select", ("Single", "Twin")),
            ("motor_rpm", "RPM", "number", "RPM"),
            ("motor_full_load_current", "Full Load Current", "number", "Amps"),
            ("motor_current_drawn", "Current Drawn", "number", "Amps"),
        ),
    ),
    (
        "Fan",
        (
            ("fan_serial_no", "Fan Sl. No.", "text", ""),
            ("fan_diameter_type", "Diameter & Type", "text", ""),
            ("fan_hardware", "Fan Hardware", "select", ("HDG", "SS304", "SS316")),
            ("fan_no_of_blades", "No. of Blades", "integer", ""),
            ("fan_pitch_angle", "Fan Pitch Angle", "number", "°"),
            ("fan_hub_cover", "Fan Hub Cover", "select", ("Yes", "No")),
        ),
    ),
    (
        "Fan Cylinder",
        (
            ("fan_cylinder_height", "Height", "number", "Inches"),
            (
                "fan_cylinder_material",
                "Material of Construction",
                "select",
                ("FRP", "Plywood"),
            ),
            ("blade_tip_clearance", "Blade Tip Clearance", "number", "Inches"),
            (
                "blade_tip_track_variation",
                "Blade Tip Track Variation",
                "number",
                "Inches",
            ),
        ),
    ),
    (
        "Drive Shafts",
        (
            ("drive_shaft_series", "Series", "select", ("6Q", "175")),
            ("drive_shaft_class", "Class", "select", ("CL I", "CL II", "CL III")),
            ("drive_shaft_oal", "OAL (Overall Length)", "number", ""),
            ("drive_shaft_oal_unit", "OAL Unit", "select", ("cm", "inches")),
            ("drive_shaft_serial_no", "Serial No.", "text", ""),
        ),
    ),
    (
        "Gearboxes",
        (
            ("gearbox_series", "Series", "text", ""),
            ("gearbox_ratio", "Ratio", "text", ""),
            ("gearbox_serial_no", "Sl. No.", "text", ""),
            ("gearbox_model_no", "Model No.", "text", ""),
        ),
    ),
    (
        "Fill",
        (
            ("fill_type", "Type (Specify nomenclature)", "text", ""),
            ("fill_material", "Material of Construction", "select", ("PVC", "Timber")),
        ),
    ),
)


def display_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def page1_values(report: EcrReport) -> dict:
    record = report.page1
    values = {
        name: display_value(getattr(record, name, None))
        for name in Page1DraftInput.model_fields
        if name != "blade_serials"
    }
    values["blade_serials"] = (
        [row.serial_no for row in record.blade_serials] if record else []
    )
    return values


async def page1_form_snapshot(request: Request) -> dict | None:
    """Only a marked full Page 1 snapshot replaces technical data.

    Existing date-only clients remain compatible and cannot erase technical data.
    Ownership/branch IDs are never extracted from browser fields.
    """
    form = await request.form()
    if form.get("page1_present") != "1":
        return None
    return {
        **{
            name: form.get(name, "")
            for name in Page1DraftInput.model_fields
            if name != "blade_serials"
        },
        "blade_serials": form.getlist("blade_serials"),
    }


def save_page1(db: Session, report: EcrReport, data: Page1DraftInput) -> None:
    record = report.page1
    if record is None:
        record = EcrPage1Technical(report=report)
        db.add(record)
    for name, value in data.model_dump(exclude={"blade_serials"}).items():
        setattr(record, name, value)
    # Replace the ordered snapshot idempotently; flush removals before reusing positions.
    record.blade_serials.clear()
    db.flush()
    record.blade_serials.extend(
        EcrFanBladeSerial(position=i, serial_no=serial)
        for i, serial in enumerate(data.blade_serials, start=1)
    )
    report.updated_at = utc_now()
