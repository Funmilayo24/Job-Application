from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings
from app.db import Database
from app.documents import write_application_documents
from app.openai_service import OpenAIJobService, load_candidate_profile

BASE_DIR = Path(__file__).resolve().parent
ARTIFACT_ROOT = Path("artifacts").resolve()
APPLICATION_STATUSES = {
    "discovered": "Not reviewed",
    "shortlisted": "Shortlisted",
    "tailoring": "Preparing application",
    "ready": "Ready to apply",
    "applied": "Applied",
    "interview": "Interview",
    "rejected": "Not progressing",
}

app = FastAPI(title="SponsorPath", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("DASHBOARD_SESSION_SECRET") or secrets.token_urlsafe(32),
    same_site="lax",
    https_only=os.getenv("DASHBOARD_SECURE_COOKIES", "").lower()
    in {"1", "true", "yes", "on"},
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _settings() -> Settings:
    # The dashboard does not send email or discover jobs, so it should not
    # require the worker-only Resend and job-board credentials.
    return Settings.from_env(require_secrets=False)


def _database() -> Database:
    database = Database(_settings().database_url)
    database.initialise()
    return database


def _configured_users() -> dict[str, str]:
    users: dict[str, str] = {}
    for index in (1, 2):
        username = os.getenv(f"DASHBOARD_USER_{index}", "").strip()
        password = os.getenv(f"DASHBOARD_PASSWORD_{index}", "")
        if username and password:
            users[username] = password
    return users


def require_user(request: Request) -> str | None:
    users = _configured_users()
    if not users:
        return None
    username = str(request.session.get("user") or "")
    if username in users:
        return username
    raise HTTPException(
        status_code=303,
        headers={"Location": f"/login?next={quote(request.url.path)}"},
    )


def _redirect(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{path}{separator}message={quote(message)}",
        status_code=303,
    )


def resolve_artifact_path(stored_path: str) -> Path:
    path = Path(stored_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ARTIFACT_ROOT) or not resolved.is_file():
        raise ValueError("Document file not found")
    return resolved


def next_scheduled_search(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> datetime:
    timezone = ZoneInfo(settings.timezone)
    current = (now or datetime.now(UTC)).astimezone(timezone)
    for day_offset in (0, 1):
        day = current.date() + timedelta(days=day_offset)
        for hour in settings.search_hours:
            candidate = datetime.combine(day, time(hour=hour), tzinfo=timezone)
            if candidate > current:
                return candidate
    raise RuntimeError("No scheduled search time is configured")


@app.get("/login")
def login_page(
    request: Request,
    next_path: str = Query(default="/", alias="next", max_length=200),
    error: str = Query(default="", max_length=200),
):
    if not _configured_users():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_path": next_path, "error": error},
    )


@app.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_path: Annotated[str, Form()] = "/",
):
    users = _configured_users()
    expected = users.get(username)
    if expected is None or not secrets.compare_digest(password, expected):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_path": next_path,
                "error": "The username or password is incorrect.",
            },
            status_code=401,
        )
    request.session.clear()
    request.session["user"] = username
    destination = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    return RedirectResponse(destination, status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def dashboard(
    request: Request,
    _user: Annotated[str | None, Depends(require_user)],
    q: str = Query(default="", max_length=100),
    qualification: str = Query(default="qualified", max_length=50),
    application: str = Query(default="", max_length=50),
    message: str = Query(default="", max_length=200),
    error: str = Query(default="", max_length=300),
):
    database = _database()
    settings = _settings()
    manual_search = database.manual_search_status()
    if manual_search["latest_run_at"]:
        manual_search["latest_run_local"] = manual_search["latest_run_at"].astimezone(
            ZoneInfo(settings.timezone)
        )
    jobs = database.dashboard_jobs(
        query=q.strip(),
        qualification=qualification,
        application_status=application,
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "jobs": jobs,
            "metrics": database.dashboard_metrics(),
            "q": q,
            "qualification": qualification,
            "application": application,
            "application_statuses": APPLICATION_STATUSES,
            "message": message,
            "error": error,
            "manual_search": manual_search,
            "next_scheduled_search": next_scheduled_search(settings),
        },
    )


@app.post("/search/manual")
def request_manual_search(
    _user: Annotated[str | None, Depends(require_user)],
):
    result = _database().queue_manual_search(_user or "local-user")
    if not result["accepted"]:
        return RedirectResponse(
            f"/?error={quote(str(result['reason']))}",
            status_code=303,
        )
    return _redirect(
        "/",
        "Search queued. The worker will start it within about one minute.",
    )


@app.get("/jobs/{job_id}")
def job_detail(
    request: Request,
    job_id: int,
    _user: Annotated[str | None, Depends(require_user)],
    message: str = Query(default="", max_length=200),
    error: str = Query(default="", max_length=300),
):
    database = _database()
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(
        request=request,
        name="job_detail.html",
        context={
            "job": job,
            "documents": database.job_documents(job_id),
            "application_statuses": APPLICATION_STATUSES,
            "message": message,
            "error": error,
        },
    )


@app.post("/jobs/{job_id}/status")
def update_status(
    job_id: int,
    status: Annotated[str, Form()],
    _user: Annotated[str | None, Depends(require_user)],
):
    if status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid application status")
    if not _database().update_application_status(job_id, status):
        raise HTTPException(status_code=404, detail="Job not found")
    return _redirect(f"/jobs/{job_id}", "Application status updated")


@app.post("/jobs/{job_id}/tailor")
def tailor_job(
    job_id: int,
    _user: Annotated[str | None, Depends(require_user)],
):
    settings = _settings()
    if not settings.openai_api_key:
        return RedirectResponse(
            f"/jobs/{job_id}?error={quote('OPENAI_API_KEY is not configured')}",
            status_code=303,
        )
    database = Database(settings.database_url)
    database.initialise()
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    database.update_application_status(job_id, "tailoring")
    try:
        service = OpenAIJobService(
            settings.openai_api_key,
            search_model=settings.search_model,
            tailor_model=settings.tailor_model,
        )
        content = service.tailor(job)
        profile = load_candidate_profile()
        cv_path, letter_path, notes_path = write_application_documents(
            job,
            content,
            candidate_name=profile["name"],
            output_root=ARTIFACT_ROOT,
        )
        database.save_documents(
            job_id,
            cv_path=str(cv_path),
            cover_letter_path=str(letter_path),
            review_notes_path=str(notes_path),
            model=settings.tailor_model,
        )
        database.update_application_status(job_id, "ready")
    except Exception as exc:
        database.update_application_status(job_id, "shortlisted")
        return RedirectResponse(
            f"/jobs/{job_id}?error={quote(str(exc)[:280])}",
            status_code=303,
        )
    return _redirect(
        f"/jobs/{job_id}",
        "Tailored CV, cover letter and review notes created",
    )


@app.get("/documents/{document_id}/{kind}")
def download_document(
    document_id: int,
    kind: str,
    _user: Annotated[str | None, Depends(require_user)],
):
    columns = {
        "cv": ("cv_path", "tailored-cv.docx"),
        "cover-letter": ("cover_letter_path", "cover-letter.docx"),
        "review-notes": ("review_notes_path", "review-notes.txt"),
    }
    if kind not in columns:
        raise HTTPException(status_code=404, detail="Document type not found")
    document = _database().get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    column, filename = columns[kind]
    stored_path = document.get(column)
    if not stored_path:
        raise HTTPException(status_code=404, detail="Document file not recorded")
    try:
        resolved = resolve_artifact_path(str(stored_path))
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Document file not found"
        ) from None
    return FileResponse(resolved, filename=filename)


@app.get("/health")
def health() -> dict[str, str]:
    _database()
    return {"status": "ok"}
