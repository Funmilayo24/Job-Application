from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.web as web


def test_resolve_artifact_path_accepts_file_inside_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    document = root / "cv.docx"
    document.write_bytes(b"document")
    monkeypatch.setattr(web, "ARTIFACT_ROOT", root.resolve())
    assert web.resolve_artifact_path(str(document)) == document.resolve()


def test_resolve_artifact_path_rejects_file_outside_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(web, "ARTIFACT_ROOT", root.resolve())
    with pytest.raises(ValueError):
        web.resolve_artifact_path(str(outside))


def test_login_protects_dashboard_and_accepts_configured_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_USER_1", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD_1", "correct-password")
    client = TestClient(web.app)

    login_page = client.get("/login")
    assert 'href="/static/style.css"' in login_page.text
    assert 'src="/static/app.js"' in login_page.text

    protected = client.get("/", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"].startswith("/login")

    rejected = client.post(
        "/login",
        data={"username": "owner", "password": "wrong", "next_path": "/"},
    )
    assert rejected.status_code == 401

    accepted = client.post(
        "/login",
        data={
            "username": "owner",
            "password": "correct-password",
            "next_path": "/",
        },
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/"
    assert "session" in accepted.cookies


def test_manual_search_request_is_queued_by_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDatabase:
        requested_by = ""

        def queue_manual_search(self, requested_by: str) -> dict:
            self.requested_by = requested_by
            return {"accepted": True}

    database = FakeDatabase()
    monkeypatch.setattr(web, "_database", lambda: database)
    response = web.request_manual_search("owner")
    assert response.status_code == 303
    assert "Search%20queued" in response.headers["location"]
    assert database.requested_by == "owner"


def test_manual_search_request_reports_server_side_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = SimpleNamespace(
        queue_manual_search=lambda _user: {
            "accepted": False,
            "reason": "Only one manual search is allowed every 24 hours.",
        }
    )
    monkeypatch.setattr(web, "_database", lambda: database)
    response = web.request_manual_search("sister")
    assert response.status_code == 303
    assert "Only%20one%20manual%20search" in response.headers["location"]
