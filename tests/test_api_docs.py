"""API documentation is registered locally, never in production."""

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.main import create_app


def documentation_app(app_settings, environment, debug=False):
    application = create_app(
        app_settings.model_copy(update={"app_env": environment, "app_debug": debug})
    )

    def unexpected_database_access():
        raise AssertionError("Documentation/health must not access sessions or reports")

    application.dependency_overrides[get_db_session] = unexpected_database_access
    return application


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
@pytest.mark.parametrize("debug", [False, True])
def test_local_api_documentation_remains_available(
    app_settings, environment, path, debug
):
    application = documentation_app(app_settings, environment, debug)
    with TestClient(application, follow_redirects=False) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert "location" not in response.headers
    if path == "/openapi.json":
        schema = response.json()
        assert schema["info"] == {"title": app_settings.app_name, "version": "0.1.0"}
        assert "/health" in schema["paths"]
    else:
        assert response.headers["content-type"].startswith("text/html")
        assert "/openapi.json" in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
@pytest.mark.parametrize("debug", [False, True])
def test_production_api_documentation_is_an_unavailable_route(
    app_settings, path, debug
):
    application = documentation_app(app_settings, "production", debug)
    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None
    assert path not in {getattr(route, "path", None) for route in application.routes}
    with TestClient(application, follow_redirects=False) as client:
        response = client.get(path)
        assert not client.cookies
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert "location" not in response.headers
    assert "set-cookie" not in response.headers
    assert "swagger" not in response.text.lower()
    assert "redoc" not in response.text.lower()
    assert "openapi" not in response.text.lower()


def test_production_health_remains_public_and_minimal(app_settings):
    application = documentation_app(app_settings, "production")
    with TestClient(application, follow_redirects=False) as client:
        response = client.get("/health")
        assert not client.cookies
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "location" not in response.headers
    assert "set-cookie" not in response.headers
