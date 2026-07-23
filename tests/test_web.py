from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.web as web
from app.vacancy_validator import VacancyValidation


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


def test_tailoring_stops_before_openai_when_link_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDatabase:
        updated_status = ""

        def initialise(self) -> None:
            pass

        def get_job(self, _job_id: int) -> dict:
            return {
                "id": 1,
                "vacancy_url": "https://example.test/missing",
                "link_status": "unverified",
                "expiry_status": "unknown",
                "sponsor_register_match": True,
                "fit_score": 90,
                "explicit_sponsorship": True,
                "salary_rule_status": "meets",
            }

        def update_vacancy_validation(self, _job_id: int, **values) -> None:
            self.updated_status = values["qualification_status"]

    class FakeValidator:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def validate(self, _job: dict) -> VacancyValidation:
            return VacancyValidation("broken", "unknown", None)

    database = FakeDatabase()
    monkeypatch.setattr(
        web,
        "_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://unused",
            min_fit_score=65,
            openai_api_key="must-not-be-used",
        ),
    )
    monkeypatch.setattr(web, "Database", lambda _url: database)
    monkeypatch.setattr(web, "VacancyValidator", FakeValidator)
    monkeypatch.setattr(
        web,
        "OpenAIJobService",
        lambda *_args, **_kwargs: pytest.fail("OpenAI must not be called"),
    )

    response = web.tailor_job(1, None)
    assert response.status_code == 303
    assert "no%20longer%20works" in response.headers["location"]
    assert database.updated_status == "rejected_broken_link"
