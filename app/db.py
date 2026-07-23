from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


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
                    vacancy_url, posted_at, closing_at, summary, career_track, fit_score,
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
                    %(salary_text)s, %(source_name)s, %(vacancy_url)s, %(posted_at)s,
                    %(closing_at)s, %(summary)s, %(career_track)s, %(fit_score)s,
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
