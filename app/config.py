from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str
    resend_api_key: str
    email_from: str
    email_to: str
    timezone: str
    search_hours: tuple[int, ...]
    run_on_startup: bool
    search_model: str
    review_model: str
    tailor_model: str
    max_discovered_jobs: int
    min_fit_score: int
    sponsor_match_threshold: int
    dry_run_email: bool
    adzuna_app_id: str
    adzuna_app_key: str
    adzuna_results_per_query: int
    adzuna_max_candidates: int
    reed_api_key: str
    reed_results_per_query: int
    reed_max_candidates: int
    possible_email_limit: int

    @classmethod
    def from_env(cls, *, require_secrets: bool = True) -> Settings:
        hours = tuple(
            sorted(
                {
                    int(value.strip())
                    for value in os.getenv("SEARCH_HOURS", "8,18").split(",")
                    if value.strip()
                }
            )
        )
        if not hours or any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError("SEARCH_HOURS must contain comma-separated hours between 0 and 23")

        values = {
            "database_url": os.getenv(
                "DATABASE_URL", "postgresql://job_agent:job_agent@localhost:5432/job_agent"
            ),
            "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
            "resend_api_key": os.getenv("RESEND_API_KEY", ""),
            "email_from": os.getenv("EMAIL_FROM", ""),
            "email_to": os.getenv("EMAIL_TO", ""),
        }
        if require_secrets:
            missing = [name.upper() for name, value in values.items() if not value]
            if missing:
                raise ValueError(f"Missing required settings: {', '.join(missing)}")

        return cls(
            **values,
            timezone=os.getenv("TIMEZONE", "Europe/London"),
            search_hours=hours,
            run_on_startup=_bool("RUN_ON_STARTUP"),
            search_model=os.getenv("OPENAI_SEARCH_MODEL", "gpt-5.6-luna"),
            review_model=os.getenv("OPENAI_REVIEW_MODEL", "gpt-5.6-terra"),
            tailor_model=os.getenv("OPENAI_TAILOR_MODEL", "gpt-5.6-terra"),
            max_discovered_jobs=_int("MAX_DISCOVERED_JOBS", 60),
            min_fit_score=_int("MIN_FIT_SCORE", 65),
            sponsor_match_threshold=_int("SPONSOR_MATCH_THRESHOLD", 92),
            dry_run_email=_bool("DRY_RUN_EMAIL"),
            adzuna_app_id=os.getenv("ADZUNA_APP_ID", ""),
            adzuna_app_key=os.getenv("ADZUNA_APP_KEY", ""),
            adzuna_results_per_query=_int("ADZUNA_RESULTS_PER_QUERY", 10),
            adzuna_max_candidates=_int("ADZUNA_MAX_CANDIDATES", 40),
            reed_api_key=os.getenv("REED_API_KEY", ""),
            reed_results_per_query=_int("REED_RESULTS_PER_QUERY", 10),
            reed_max_candidates=_int("REED_MAX_CANDIDATES", 40),
            possible_email_limit=_int("POSSIBLE_EMAIL_LIMIT", 15),
        )
