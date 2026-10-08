"""One final validator and transactional workflow/audit rules for every role."""

import json
from collections import defaultdict

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.audit.models import ReportAuditEvent
from app.core.time import utc_now
from app.ecr import batch_c, page1, page2, page3
from app.ecr.models import EcrReportStatus
from app.ecr.series import COOLING_TOWER_SERIES, active_fields, active_sections


def final_errors(report) -> dict[str, list[str]]:
    """Validate stored data, not browser completeness flags; False/0 are data."""
    errors = defaultdict(list)
    package = report.tower.package
    for name, label in (
        ("cooling_tower_serial_no", "Cooling Tower Serial No."),
        ("customer", "Customer"),
        ("model", "Model"),
        ("place_of_installation", "Place of Installation"),
    ):
        if not getattr(package, name, None):
            errors["Report Details"].append(f"{label} is required.")
    if package.cooling_tower_series not in COOLING_TOWER_SERIES:
        errors["Report Details"].append("Select an approved Cooling Tower Series.")
    if report.erection_completion_date is None:
        errors["Report Details"].append("Erection Completion Date is required.")

    series = package.cooling_tower_series
    for module, schema, values in (
        (page1, page1.Page1DraftInput, page1.page1_values(report)),
        (page2, page2.Page2DraftInput, page2.page2_values(report)),
    ):
        fields = active_fields(module.SECTIONS, series)
        payload = {
            k: v for k, v in values.items() if k in fields or k == "blade_serials"
        }
        labels = {
            n: (section, label)
            for section, items in active_sections(module.SECTIONS, series)
            for n, label, *_ in items
        }
        for name in module.required_fields(series):
            if payload.get(name) in (None, ""):
                section, label = labels[name]
                errors[section].append(f"{label} is required.")
        try:
            schema.model_validate(payload)
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False):
                field = error["loc"][0] if error["loc"] else None
                section, label = labels.get(field, ("Technical Data", "Value"))
                errors[section].append(f"{label}: {error['msg']}")

    values = batch_c.values(report)
    for name in batch_c.FINAL_REQUIRED_FIELDS:
        if values.get(name) in (None, ""):
            section = "Other Optionals" if name in batch_c.SWITCH_FIELDS else "DE / NDE"
            errors[section].append(f"{name.replace('_', ' ').upper()} is required.")
    try:
        batch_c.BatchCDraftInput.model_validate(values)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            errors["Page 2 — Alignment / Torque"].append(error["msg"])
    fan_rows = [r for r in report.fastener_rows if r.category == "FAN_HARDWARE"]
    if not fan_rows:
        errors["Fastener Torque"].append(
            "At least one complete Fan Hardware Fastener row is required."
        )
    for row in report.fastener_rows:
        if any(
            getattr(row, n) in (None, "") for n in ("diameter", "torque", "torque_unit")
        ):
            label = dict(batch_c.CATEGORIES)[row.category]
            errors["Fastener Torque"].append(
                f"{label} row {row.sequence_no}: Diameter, Torque and Torque Unit are required."
            )
    text = page3.values(report)
    if not text["team_leader_report"].strip():
        errors["Completion Details"].append(
            "Detailed Report by the Erection Team-Leader is required."
        )
    try:
        page3.Page3DraftInput.model_validate(text)
    except ValidationError as exc:
        errors["Completion Details"].extend(
            e["msg"] for e in exc.errors(include_input=False)
        )
    # Customer signature/comment and deferred JCC/photos are intentionally absent.
    return dict(errors)


def audit(db: Session, report, actor, action, field, old, new):
    encode = lambda value: (
        None if value is None else json.dumps(value, ensure_ascii=False, default=str)
    )
    db.add(
        ReportAuditEvent(
            report_id=report.id,
            actor_user_id=actor.id,
            actor_role=actor.role.value,
            action=action,
            field_name=field,
            old_value=encode(old),
            new_value=encode(new),
        )
    )


def transition(db, report, actor, action):
    """Caller locks an authorized report; transition and audit commit together."""
    target = {
        "review": (EcrReportStatus.DRAFT, EcrReportStatus.REVIEWED),
        "back-to-edit": (EcrReportStatus.REVIEWED, EcrReportStatus.DRAFT),
        "submit": (EcrReportStatus.REVIEWED, EcrReportStatus.SUBMITTED),
        "approve": (EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED),
    }
    old, new = target[action]
    if report.status is not old:
        raise ValueError(
            f"This action requires a {old.value} report. Reload to see its current status."
        )
    if action != "back-to-edit":
        errors = final_errors(report)
        if errors:
            return errors
    now = utc_now()
    report.status = new
    report.updated_at = now
    if action == "review":
        report.reviewed_at = now
    elif action == "back-to-edit":
        report.reviewed_at = None  # Re-editing invalidates the previous review.
    elif action == "submit":
        # Official identity is immutable after submission; never supplied by a form.
        report.supervisor_name_snapshot = report.supervisor.full_name
        report.supervisor_employee_id_snapshot = report.supervisor.employee_id
        report.submitted_at = now
    else:
        report.approved_at = now
        report.approved_by_user_id = actor.id
    audit(
        db,
        report,
        actor,
        action.upper().replace("-", "_"),
        "status",
        old.value,
        new.value,
    )
    return {}


def report_snapshot(report):
    """Report-scoped fields only: never include signature bytes/keys or secrets."""
    return {
        "erection_start_date": str(report.erection_start_date)
        if report.erection_start_date
        else None,
        "erection_completion_date": str(report.erection_completion_date)
        if report.erection_completion_date
        else None,
        **{f"page1.{k}": v for k, v in page1.page1_values(report).items()},
        **{f"page2.{k}": v for k, v in page2.page2_values(report).items()},
        **{f"batch_c.{k}": v for k, v in batch_c.values(report).items()},
        **{f"page3.{k}": v for k, v in page3.values(report).items()},
    }


def audit_edits(db, report, actor, before):
    after = report_snapshot(report)
    for name in before:
        if before[name] != after[name]:
            audit(db, report, actor, "ADMIN_EDIT", name, before[name], after[name])
