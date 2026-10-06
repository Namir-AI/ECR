"""All local CSS/JS use a startup bundle version; no Git/config secrets needed."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.static_assets import static_bundle_version
from app.main import create_app
from tests.conftest import csrf_from, login
from tests.test_phase3 import _create_report


def urls(html):
    return re.findall(
        r'(?:href|src)="([^"]+/static/[^"?]+\.(?:css|js)(?:\?[^" ]*)?)"', html
    )


def assert_versioned(html, version):
    references = urls(html)
    assert references
    for url in references:
        assert parse_qs(urlsplit(url).query) == {"v": [version]}
    return references


def test_development_version_is_stable_and_content_based(
    tmp_path, monkeypatch, app_settings
):
    monkeypatch.delenv("STATIC_ASSET_VERSION", raising=False)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.css").write_text("body { color: blue; }")
    old = static_bundle_version(assets)
    assert old == static_bundle_version(assets) and re.fullmatch("[a-f0-9]{20}", old)
    (assets / "app.css").write_text("body { color: orange; }")
    assert static_bundle_version(assets) != old
    (assets / "shell.js").write_text("console.log('new release')")
    new = static_bundle_version(assets)
    (assets / "secret.env").write_text("DB_PASSWORD=do-not-expose")
    assert static_bundle_version(assets) == new
    assert create_app(app_settings).state.static_asset_version


def test_static_version_is_computed_at_startup_not_per_request(client, monkeypatch):
    version = client.app.state.static_asset_version
    monkeypatch.setattr(
        "app.main.static_bundle_version", lambda _path: pytest.fail("per-request hash")
    )
    for _ in range(2):
        references = assert_versioned(client.get("/auth/login").text, version)
        assert any("/app.css?v=" in url for url in references)
        assert any("/app_shell.js?v=" in url for url in references)
        assert any("/password_visibility.js?v=" in url for url in references)
    assert client.get(references[0]).status_code == 200


def test_all_ecr_template_scripts_share_running_version(
    client, db_session, user_factory
):
    owner = user_factory()
    report = _create_report(db_session, owner)
    login(client, owner.employee_id)
    version = client.app.state.static_asset_version
    paths = (
        "/ecr/reports/new",
        f"/ecr/reports/{report.id}/edit",
        f"/ecr/reports/{report.id}",
        f"/ecr/packages/{report.tower.package.id}/attachments",
    )
    scripts = set()
    start = client.get("/ecr/reports/new")
    create = client.post(
        "/ecr/reports/new/check",
        data={
            "csrf_token": csrf_from(start.text),
            "cooling_tower_serial_no": report.tower.package.cooling_tower_serial_no,
        },
    )
    assert create.status_code == 200
    scripts.update(
        urlsplit(url).path.rsplit("/", 1)[-1]
        for url in assert_versioned(create.text, version)
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200
        scripts.update(
            urlsplit(url).path.rsplit("/", 1)[-1]
            for url in assert_versioned(response.text, version)
        )
    assert {
        "app.css",
        "app_shell.js",
        "password_visibility.js",
        "new_report.js",
        "nonnegative_decimal.js",
        "series_confirmation.js",
        "reading_instruments.js",
        "ecr_autosave.js",
        "customer_signature.js",
        "package_attachments.js",
    } <= scripts
    # A different running bundle produces different URLs, without touching .env.
    client.app.state.static_asset_version = "0123456789abcdefabcd"
    updated = client.get("/dashboard").text
    assert_versioned(updated, "0123456789abcdefabcd")
    assert owner.password_hash not in updated


def test_new_running_bundle_gets_new_version_without_deployment_env(
    tmp_path, monkeypatch, app_settings
):
    (tmp_path / "app.css").write_text("body { color: blue; }")
    monkeypatch.setattr("app.main.STATIC_DIRECTORY", tmp_path)
    first = create_app(app_settings)
    (tmp_path / "app.css").write_text("body { color: orange; }")
    second = create_app(app_settings)
    assert first.state.static_asset_version != second.state.static_asset_version


def test_no_manual_css_js_versioning_remains():
    from app.core.templates import TEMPLATE_DIRECTORY

    for path in TEMPLATE_DIRECTORY.rglob("*.html"):
        html = path.read_text()
        assert not re.search(r"url_for\('static', path='[^']+\.(?:css|js)'\)", html), (
            path
        )
        assert not re.search(r"\?v=\d", html), path
