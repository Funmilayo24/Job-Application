from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _settings() -> Settings:
    return Settings.from_env()


def _database() -> Database:
    database = Database(_settings().database_url)
    database.initialise()
    return database


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


@app.get("/")
def dashboard(
    request: Request,
    q: str = Query(default="", max_length=100),
    qualification: str = Query(default="qualified", max_length=50),
    application: str = Query(default="", max_length=50),
    message: str = Query(default="", max_length=200),
):
    database = _database()
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
        },
    )


@app.get("/jobs/{job_id}")
def job_detail(
    request: Request,
    job_id: int,
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
):
    if status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid application status")
    if not _database().update_application_status(job_id, status):
        raise HTTPException(status_code=404, detail="Job not found")
    return _redirect(f"/jobs/{job_id}", "Application status updated")


@app.post("/jobs/{job_id}/tailor")
def tailor_job(job_id: int):
    settings = _settings()
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
def download_document(document_id: int, kind: str):
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
