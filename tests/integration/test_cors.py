import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

LOCAL_FRONTEND_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


@pytest.mark.parametrize("origin", LOCAL_FRONTEND_ORIGINS)
def test_cors_preflight_allows_local_frontend_origins(origin: str):
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", LOCAL_FRONTEND_ORIGINS)
def test_cors_real_response_includes_local_origin(origin: str):
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/health",
            headers={"Origin": origin},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_does_not_authorize_unconfigured_origin():
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_configuration_has_no_wildcard_and_disables_credentials():
    settings = get_settings()

    assert set(settings.cors_origins) == set(LOCAL_FRONTEND_ORIGINS)
    assert "*" not in settings.cors_origins
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": LOCAL_FRONTEND_ORIGINS[0],
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert "access-control-allow-credentials" not in response.headers
