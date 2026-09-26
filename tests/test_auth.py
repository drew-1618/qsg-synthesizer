"""Tests for the optional HTTP Basic Auth gate.

Mirrors the project's "optional credential" pattern: with no
APP_USERNAME/APP_PASSWORD configured (the default in tests and local dev),
every route must stay open, since the rest of the suite assumes
unauthenticated access.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_open_by_default_without_configured_credentials(monkeypatch):
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    assert client.get("/api/v1/samples").status_code == 200
    assert client.get("/").status_code == 200


def test_healthz_never_requires_auth(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    assert client.get("/healthz").status_code == 200


def test_protected_route_requires_auth_once_configured(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    response = client.get("/api/v1/samples")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_correct_credentials_are_accepted(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    response = client.get("/api/v1/samples", auth=("admin", "hunter2"))
    assert response.status_code == 200


def test_wrong_password_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    response = client.get("/api/v1/samples", auth=("admin", "wrong"))
    assert response.status_code == 401


def test_wrong_username_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    response = client.get("/api/v1/samples", auth=("someone-else", "hunter2"))
    assert response.status_code == 401


def test_static_files_open_by_default_without_configured_credentials(monkeypatch):
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    assert client.get("/static/app.js").status_code == 200


def test_static_files_require_auth_once_configured(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")

    response = client.get("/static/app.js")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"

    ok = client.get("/static/app.js", auth=("admin", "hunter2"))
    assert ok.status_code == 200
