"""FastAPI application and health endpoint tests."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_application_starts_and_health_returns_200() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
