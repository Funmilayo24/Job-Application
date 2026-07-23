from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

SEARCH_LOCK_KEY = 8_240_724_901
MANUAL_QUEUE_LOCK_KEY = 8_240_724_902
MANUAL_COOLDOWN = timedelta(hours=24)
SEARCH_FRESHNESS = timedelta(hours=6)


def evaluate_manual_search_limits(
    *,
    now: datetime,
    latest_run_at: datetime | None,
    latest_manual_at: datetime | None,
    active_request: dict[str, Any] | None,
) -> dict[str, Any]:
    next_allowed_candidates: list[tuple[datetime, str]] = []
    if latest_run_at:
        next_allowed_candidates.append(
            (
                latest_run_at + SEARCH_FRESHNESS,
                "Please wait at least six hours after the last search.",
            )
        )
    if latest_manual_at:
        next_allowed_candidates.append(
            (
                latest_manual_at + MANUAL_COOLDOWN,
                "Only one manual search is allowed every 24 hours.",
            )
        )

    future_limits = [
        (allowed_at, reason)
        for allowed_at, reason in next_allowed_candidates
        if allowed_at > now
    ]
    next_allowed_at = max(
        (allowed_at for allowed_at, _reason in future_limits),
        default=None,
    )
    reason = ""
    if active_request:
        state = str(active_request["status"])
        reason = "A manual search is running." if state == "running" else "A search is queued."
    elif next_allowed_at:
        reason = next(
            limit_reason
            for allowed_at, limit_reason in future_limits
            if allowed_at == next_allowed_at
        )

    return {
        "can_request": not active_request and next_allowed_at is None,
        "reason": reason,
        "next_allowed_at": next_allowed_at,
        "active_request": active_request,
        "latest_run_at": latest_run_at,
        "latest_manual_at": latest_manual_at,
        "server_now": now,
    }


class Database:
    def __init__(self, url: str) -> None:
        self.url = url

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.url, row_factory=dict_row) as connection:
            yield connection

    def initialise(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(schema)

    @contextmanager
    def search_lock(self) -> Iterator[bool]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired",
                (SEARCH_LOCK_KEY,),
            )
            acquired = bool(cursor.fetchone()["acquired"])
            try:
                yield acquired
            finally:
                if acquired:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (SEARCH_LOCK_KEY,))

    def start_run(self) -> int:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO search_runs DEFAULT VALUES RETURNING id")
            return int(cursor.fetchone()["id"])

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        discovered: int = 0,
        qualified: int = 0,
        emailed: int = 0,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE search_runs
                SET finished_at = now(), status = %s, discovered_count = %s,
                    qualified_count = %s, emailed_count = %s, error_message = %s
                WHERE id = %s
                """,
                (status, discovered, qualified, emailed, error, run_id),
            )

    @staticmethod
    def _manual_search_status(cursor: psycopg.Cursor) -> dict[str, Any]:
        cursor.execute("SELECT now() AS server_now")
        now = cursor.fetchone()["server_now"]
        cursor.execute(
            """
            SELECT started_at
            FROM search_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        latest_run = cursor.fetchone()
        cursor.execute(
            """
            SELECT requested_at
            FROM manual_search_requests
            ORDER BY requested_at DESC
            LIMIT 1
            """
        )
        latest_manual = cursor.fetchone()
        cursor.execute(
            """
            SELECT id, status, requested_at, requested_by, started_at
            FROM manual_search_requests
            WHERE status IN ('pending', 'running')
            ORDER BY requested_at
            LIMIT 1
            """
        )
        active_request = cursor.fetchone()
        return evaluate_manual_search_limits(
            now=now,
            latest_run_at=latest_run["started_at"] if latest_run else None,
            latest_manual_at=latest_manual["requested_at"] if latest_manual else None,
            active_request=active_request,
        )

    def manual_search_status(self) -> dict[str, Any]:
        with self.connect() as connection, connection.cursor() as cursor:
            return self._manual_search_status(cursor)

    def queue_manual_search(self, requested_by: str) -> dict[str, Any]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (MANUAL_QUEUE_LOCK_KEY,),
            )
            status = self._manual_search_status(cursor)
            if not status["can_request"]:
                return {"accepted": False, **status}
            cursor.execute(
                """
                INSERT INTO manual_search_requests (requested_by)
                VALUES (%s)
                RETURNING id, status, requested_at, requested_by
                """,
                (requested_by,),
            )
            request = cursor.fetchone()
            return {
                "accepted": True,
                **status,
                "active_request": request,
            }

    def claim_manual_search(self) -> dict[str, Any] | None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE manual_search_requests
                SET status = 'pending', started_at = NULL
                WHERE status = 'running'
                  AND started_at < now() - interval '2 hours'
                """
            )
            cursor.execute(
                """
                SELECT id, requested_at, requested_by
                FROM manual_search_requests
                WHERE status = 'pending'
                ORDER BY requested_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            request = cursor.fetchone()
            if not request:
                return None
            cursor.execute(
                """
                UPDATE manual_search_requests
                SET status = 'running', started_at = now(), error_message = NULL
                WHERE id = %s
                RETURNING id, status, requested_at, requested_by, started_at
                """,
                (request["id"],),
            )
            return cursor.fetchone()

    def finish_manual_search(
        self,
        request_id: int,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Manual search status must be completed or failed")
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE manual_search_requests
                SET status = %s, finished_at = now(), error_message = %s
                WHERE id = %s
                """,
                (status, error, request_id),
            )

    def requeue_manual_search(self, request_id: int) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE manual_search_requests
                SET status = 'pending', started_at = NULL
                WHERE id = %s AND status = 'running'
                """,
                (request_id,),
            )

    def replace_sponsors(self, rows: list[dict[str, str]]) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("TRUNCATE sponsors RESTART IDENTITY")
            cursor.executemany(
                """
                INSERT INTO sponsors
                    (organisation_name, normalised_name, town_city, county, type_and_rating, route)
                VALUES (%(organisation_name)s, %(normalised_name)s, %(town_city)s,
                        %(county)s, %(type_and_rating)s, %(route)s)
                """,
                rows,
            )

    def sponsor_names(self) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT organisation_name, normalised_name, route, type_and_rating
                FROM sponsors
                WHERE route ILIKE '%%Skilled Worker%%'
                """
            )
            return list(cursor.fetchall())

    def upsert_job(self, run_id: int, job: dict[str, Any]) -> tuple[int, bool]:
        payload = json.dumps(job, ensure_ascii=False)
        values = {
            **job,
            "fit_reasons": json.dumps(job.get("fit_reasons", [])),
            "missing_requirements": json.dumps(job.get("missing_requirements", [])),
            "raw_payload": payload,
            "search_run_id": run_id,
        }
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (
                    canonical_key, title, employer, location, salary_text, source_name,
                    vacancy_url, resolved_vacancy_url, link_status, link_checked_at,
                    posted_at, closing_at, expiry_status, expiry_checked_at,
                    summary, career_track, fit_score,
                    fit_reasons, missing_requirements, sponsorship_claim,
                    sponsorship_evidence, sponsorship_evidence_url, explicit_sponsorship,
                    sponsorship_exclusion, sponsorship_exclusion_evidence,
                    sponsorship_tier,
                    sponsor_register_match, sponsor_register_name, sponsor_match_score,
                    salary_rule_status, salary_rule_reason, qualification_status,
                    raw_payload, search_run_id
                )
                VALUES (
                    %(canonical_key)s, %(title)s, %(employer)s, %(location)s,
                    %(salary_text)s, %(source_name)s, %(vacancy_url)s,
                    %(resolved_vacancy_url)s, %(link_status)s, now(),
                    %(posted_at)s, %(closing_at)s, %(expiry_status)s, now(),
                    %(summary)s, %(career_track)s, %(fit_score)s,
                    %(fit_reasons)s::jsonb, %(missing_requirements)s::jsonb,
                    %(sponsorship_claim)s, %(sponsorship_evidence)s,
                    %(sponsorship_evidence_url)s, %(explicit_sponsorship)s,
                    %(sponsorship_exclusion)s, %(sponsorship_exclusion_evidence)s,
                    %(sponsorship_tier)s,
                    %(sponsor_register_match)s, %(sponsor_register_name)s,
                    %(sponsor_match_score)s, %(salary_rule_status)s,
                    %(salary_rule_reason)s, %(qualification_status)s,
                    %(raw_payload)s::jsonb, %(search_run_id)s
                )
                ON CONFLICT (canonical_key) DO UPDATE SET
                    last_seen_at = now(),
                    title = EXCLUDED.title,
                    employer = EXCLUDED.employer,
                    location = EXCLUDED.location,
                    salary_text = EXCLUDED.salary_text,
                    posted_at = EXCLUDED.posted_at,
                    closing_at = EXCLUDED.closing_at,
                    resolved_vacancy_url = EXCLUDED.resolved_vacancy_url,
                    link_status = EXCLUDED.link_status,
                    link_checked_at = EXCLUDED.link_checked_at,
                    expiry_status = EXCLUDED.expiry_status,
                    expiry_checked_at = EXCLUDED.expiry_checked_at,
                    fit_score = EXCLUDED.fit_score,
                    fit_reasons = EXCLUDED.fit_reasons,
                    missing_requirements = EXCLUDED.missing_requirements,
                    sponsorship_claim = EXCLUDED.sponsorship_claim,
                    sponsorship_evidence = EXCLUDED.sponsorship_evidence,
                    sponsorship_evidence_url = EXCLUDED.sponsorship_evidence_url,
                    explicit_sponsorship = EXCLUDED.explicit_sponsorship,
                    sponsorship_exclusion = EXCLUDED.sponsorship_exclusion,
                    sponsorship_exclusion_evidence = EXCLUDED.sponsorship_exclusion_evidence,
                    sponsorship_tier = EXCLUDED.sponsorship_tier,
                    sponsor_register_match = EXCLUDED.sponsor_register_match,
                    sponsor_register_name = EXCLUDED.sponsor_register_name,
                    sponsor_match_score = EXCLUDED.sponsor_match_score,
                    salary_rule_status = EXCLUDED.salary_rule_status,
                    salary_rule_reason = EXCLUDED.salary_rule_reason,
                    qualification_status = EXCLUDED.qualification_status,
                    raw_payload = EXCLUDED.raw_payload,
                    search_run_id = EXCLUDED.search_run_id
                RETURNING id, (xmax = 0) AS inserted
                """,
                values,
            )
            row = cursor.fetchone()
            return int(row["id"]), bool(row["inserted"])

    def pending_jobs_for_validation(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM jobs
                WHERE emailed_at IS NULL
                  AND qualification_status IN (
                      'qualified_confirmed',
                      'qualified_possible',
                      'rejected_expired',
                      'rejected_broken_link'
                  )
                  AND (
                      link_checked_at IS NULL
                      OR link_checked_at < now() - interval '6 hours'
                  )
                ORDER BY first_seen_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def update_vacancy_validation(
        self,
        job_id: int,
        *,
        resolved_vacancy_url: str | None,
        link_status: str,
        expiry_status: str,
        qualification_status: str,
        sponsorship_tier: str,
    ) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET resolved_vacancy_url = %s,
                    link_status = %s,
                    link_checked_at = now(),
                    expiry_status = %s,
                    expiry_checked_at = now(),
                    qualification_status = %s,
                    sponsorship_tier = %s
                WHERE id = %s
                """,
                (
                    resolved_vacancy_url,
                    link_status,
                    expiry_status,
                    qualification_status,
                    sponsorship_tier,
                    job_id,
                ),
            )

    def unemailed_digest_jobs(self, *, possible_limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                (
                    SELECT *
                    FROM jobs
                    WHERE qualification_status = 'qualified_confirmed'
                      AND emailed_at IS NULL
                    ORDER BY fit_score DESC, first_seen_at DESC
                )
                UNION ALL
                (
                    SELECT *
                    FROM jobs
                    WHERE qualification_status = 'qualified_possible'
                      AND emailed_at IS NULL
                    ORDER BY fit_score DESC, first_seen_at DESC
                    LIMIT %s
                )
                """,
                (possible_limit,),
            )
            return list(cursor.fetchall())

    def all_qualified_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM jobs
                WHERE qualification_status IN (
                    'qualified_confirmed',
                    'qualified_possible'
                )
                ORDER BY
                    CASE qualification_status
                        WHEN 'qualified_confirmed' THEN 0
                        ELSE 1
                    END,
                    fit_score DESC,
                    first_seen_at DESC
                """
            )
            return list(cursor.fetchall())

    def mark_emailed(self, job_ids: list[int]) -> None:
        if not job_ids:
            return
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE jobs SET emailed_at = now() WHERE id = ANY(%s)", (job_ids,))

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            return cursor.fetchone()

    def dashboard_metrics(self) -> dict[str, int]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) AS total,
                    count(*) FILTER (
                        WHERE qualification_status IN (
                            'qualified_confirmed', 'qualified_possible'
                        )
                    ) AS qualified,
                    count(*) FILTER (
                        WHERE qualification_status = 'qualified_confirmed'
                    ) AS confirmed,
                    count(*) FILTER (
                        WHERE qualification_status = 'qualified_possible'
                    ) AS possible,
                    count(*) FILTER (
                        WHERE application_status = 'applied'
                    ) AS applied
                FROM jobs
                """
            )
            row = cursor.fetchone()
            cursor.execute("SELECT count(DISTINCT job_id) AS tailored FROM tailored_documents")
            row["tailored"] = int(cursor.fetchone()["tailored"])
            return {key: int(value) for key, value in row.items()}

    def dashboard_jobs(
        self,
        *,
        query: str = "",
        qualification: str = "qualified",
        application_status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("(title ILIKE %s OR employer ILIKE %s OR location ILIKE %s)")
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        if qualification == "qualified":
            clauses.append(
                "qualification_status IN ('qualified_confirmed', 'qualified_possible')"
            )
        elif qualification:
            clauses.append("qualification_status = %s")
            params.append(qualification)
        if application_status:
            clauses.append("application_status = %s")
            params.append(application_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT jobs.*,
                       EXISTS (
                           SELECT 1 FROM tailored_documents documents
                           WHERE documents.job_id = jobs.id
                       ) AS has_documents
                FROM jobs
                {where}
                ORDER BY
                    CASE qualification_status
                        WHEN 'qualified_confirmed' THEN 0
                        WHEN 'qualified_possible' THEN 1
                        ELSE 2
                    END,
                    fit_score DESC,
                    first_seen_at DESC
                LIMIT %s
                """,
                params,
            )
            return list(cursor.fetchall())

    def job_documents(self, job_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM tailored_documents
                WHERE job_id = %s
                ORDER BY created_at DESC
                """,
                (job_id,),
            )
            return list(cursor.fetchall())

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM tailored_documents WHERE id = %s",
                (document_id,),
            )
            return cursor.fetchone()

    def update_application_status(self, job_id: int, status: str) -> bool:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET application_status = %s
                WHERE id = %s
                RETURNING id
                """,
                (status, job_id),
            )
            return cursor.fetchone() is not None

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection, connection.cursor() as cursor:
            if status:
                cursor.execute(
                    """
                    SELECT id, title, employer, location, salary_text, fit_score,
                           qualification_status, application_status, vacancy_url,
                           first_seen_at
                    FROM jobs
                    WHERE qualification_status = %s
                    ORDER BY fit_score DESC, first_seen_at DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, title, employer, location, salary_text, fit_score,
                           qualification_status, application_status, vacancy_url,
                           first_seen_at
                    FROM jobs
                    ORDER BY first_seen_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            return list(cursor.fetchall())

    def save_documents(
        self,
        job_id: int,
        *,
        cv_path: str,
        cover_letter_path: str,
        review_notes_path: str,
        model: str,
    ) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tailored_documents (
                    job_id, cv_path, cover_letter_path, review_notes_path, model
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (job_id, cv_path, cover_letter_path, review_notes_path, model),
            )
