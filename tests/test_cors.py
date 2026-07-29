"""Tests for Angular-compatible CORS middleware."""

import pytest
from fastapi.testclient import TestClient

from main import app


ANGULAR_ORIGIN = "http://localhost:4200"


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/dashboard/statistics", "GET"),
        ("/api/report", "GET"),
        ("/api/report/filter-options", "GET"),
        ("/api/report/export", "GET"),
        ("/api/tracking/register-reply", "POST"),
        ("/api/tracking/register-bounce", "POST"),
    ],
)
def test_angular_preflight_succeeds_for_existing_api_paths(
    path: str,
    method: str,
) -> None:
    client = TestClient(app)

    response = client.options(
        path,
        headers={
            "Origin": ANGULAR_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ANGULAR_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert method in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers[
        "access-control-allow-headers"
    ].lower()


def test_cors_headers_are_returned_for_simple_get_response() -> None:
    client = TestClient(app)

    response = client.get("/health", headers={"Origin": ANGULAR_ORIGIN})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["access-control-allow-origin"] == ANGULAR_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_unconfigured_origin_is_not_allowed() -> None:
    client = TestClient(app)

    response = client.options(
        "/api/dashboard/statistics",
        headers={
            "Origin": "https://not-configured.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
