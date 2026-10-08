"""Named application action polish; routes, ownership and checks stay unchanged."""

from html.parser import HTMLParser
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.ecr.models import EcrPackage, EcrReport
from app.users.models import UserRole
from tests.conftest import csrf_from, login
from tests.test_phase3 import _create_report


class Actions(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items, self.active = [], None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "button"):
            self.active = {"tag": tag, "attrs": dict(attrs), "text": "", "icons": []}
        elif tag == "svg" and self.active is not None:
            self.active["icons"].append(dict(attrs))

    def handle_data(self, data):
        if self.active is not None:
            self.active["text"] += data

    def handle_endtag(self, tag):
        if self.active is not None and tag == self.active["tag"]:
            self.active["text"] = " ".join(self.active["text"].split())
            self.items.append(self.active)
            self.active = None

    def action(self, label):
        matches = [item for item in self.items if item["text"] == label]
        assert len(matches) == 1
        return matches[0]


def styled_action(html, label, destination=None, primary=False):
    action = Actions(html).action(label)
    classes = action["attrs"]["class"].split()
    assert "action-button" in classes
    assert ("action-primary" if primary else "secondary") in classes
    if destination is not None:
        assert action["tag"] == "a"
        assert "button" in classes and action["attrs"]["href"] == destination
    assert len(action["icons"]) == 1
    assert action["icons"][0]["aria-hidden"] == "true"
    assert action["icons"][0]["focusable"] == "false"
    return action


@pytest.mark.parametrize("role", list(UserRole))
def test_shared_sidebar_product_name_is_prominent(client, user_factory, role):
    user = user_factory(role=role)
    login(client, user.employee_id)
    html = client.get("/dashboard").text
    assert '<strong class="product-name">Service Management</strong>' in html
    assert 'alt="Paharpur"' in html


@pytest.mark.parametrize("role", list(UserRole))
def test_report_print_and_back_are_shared_styled_actions(
    client, db_session, user_factory, role
):
    owner = user_factory()
    report = _create_report(db_session, owner)
    viewer = (
        owner
        if role is UserRole.SUPERVISOR
        else user_factory(role=role, branch=owner.branch)
    )
    login(client, viewer.employee_id)
    url = (
        f"/ecr/reports/{report.id}"
        if role is UserRole.SUPERVISOR
        else f"/reports/{report.id}"
    )
    response = client.get(url)
    assert response.status_code == 200
    styled_action(
        response.text, "Print", f"/ecr/reports/{report.id}/print", primary=True
    )
    styled_action(
        response.text,
        "Back to Reports",
        "/dashboard" if role is UserRole.SUPERVISOR else "/reports",
    )
    assert "Download PDF" not in response.text


def test_create_branch_admin_back_button_preserves_destination(client, user_factory):
    admin = user_factory(role=UserRole.SUPERADMIN)
    login(client, admin.employee_id)
    response = client.get("/admin/users/create-branch-admin")
    assert response.status_code == 200
    styled_action(response.text, "Back", "/admin/users")
    assert "← Back" not in response.text
    assert 'action="/admin/users/create-branch-admin"' in response.text


@pytest.mark.parametrize("existing", [False, True])
def test_start_report_keeps_existing_package_check_flow(
    client, db_session, user_factory, existing
):
    supervisor = user_factory()
    report = _create_report(db_session, supervisor)
    serial = (
        report.tower.package.cooling_tower_serial_no
        if existing
        else f"POLISH-{uuid4().hex}"
    )
    login(client, supervisor.employee_id)
    before = (
        db_session.scalar(select(func.count(EcrPackage.id))),
        db_session.scalar(select(func.count(EcrReport.id))),
    )
    page = client.get("/ecr/reports/new")
    assert page.status_code == 200
    assert "First identify the ECR Package." not in page.text
    assert "Check Package" not in page.text
    styled_action(page.text, "Back to Dashboard", "/dashboard")
    action = styled_action(page.text, "Start Report", primary=True)
    assert action["tag"] == "button" and action["attrs"]["type"] == "submit"
    assert 'method="post" action="/ecr/reports/new/check"' in page.text
    checked = client.post(
        "/ecr/reports/new/check",
        data={"csrf_token": csrf_from(page.text), "cooling_tower_serial_no": serial},
    )
    assert checked.status_code == 200
    assert (
        "Existing Package — shared information is read-only" in checked.text
    ) is existing
    assert "data-new-report-form" in checked.text
    assert before == (
        db_session.scalar(select(func.count(EcrPackage.id))),
        db_session.scalar(select(func.count(EcrReport.id))),
    )
