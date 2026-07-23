from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.adzuna import AdzunaClient
from app.config import Settings
from app.db import Database
from app.emailer import ResendEmailer
from app.job_logic import prepare_job
from app.openai_service import OpenAIJobService
from app.reed import ReedClient
from app.sponsor_register import SponsorMatcher, download_sponsors, normalise_company_name

logger = logging.getLogger(__name__)
_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class RunResult:
    discovered: int
    qualified: int
    emailed: int


class SearchAlreadyRunningError(RuntimeError):
    pass


class JobPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.ai = OpenAIJobService(
            settings.openai_api_key,
            search_model=settings.search_model,
            tailor_model=settings.tailor_model,
        )
        self.emailer = ResendEmailer(
            settings.resend_api_key, settings.email_from, settings.email_to
        )
        self.adzuna = (
            AdzunaClient(settings.adzuna_app_id, settings.adzuna_app_key)
            if settings.adzuna_app_id and settings.adzuna_app_key
            else None
        )
        self.reed = ReedClient(settings.reed_api_key) if settings.reed_api_key else None

    def refresh_sponsors(self) -> int:
        sponsors = download_sponsors()
        self.db.replace_sponsors(sponsors)
        logger.info("Imported %s sponsor-register rows", len(sponsors))
        return len(sponsors)

    def run(self) -> RunResult:
        self.db.initialise()
        with self.db.search_lock() as acquired:
            if not acquired:
                raise SearchAlreadyRunningError("Another search is already running")
            return self._run_locked()

    def _run_locked(self) -> RunResult:
        run_id = self.db.start_run()
        discovered = qualified = emailed = 0
        try:
            sponsor_rows = self.db.sponsor_names()
            if not sponsor_rows:
                self.refresh_sponsors()
                sponsor_rows = self.db.sponsor_names()
            matcher = SponsorMatcher(
                sponsor_rows, threshold=self.settings.sponsor_match_threshold
            )
            web_target = max(1, self.settings.max_discovered_jobs // 2)
            web_jobs = self.ai.discover_jobs(web_target)
            reviewed_jobs: list[dict] = []
            remaining = self.settings.max_discovered_jobs - len(web_jobs)
            candidate_sources: list[list[dict]] = []
            if self.adzuna:
                adzuna_candidates = self.adzuna.search(
                    self.ai.search_config,
                    results_per_query=self.settings.adzuna_results_per_query,
                    max_candidates=self.settings.adzuna_max_candidates,
                )
                logger.info(
                    "Adzuna returned %s candidates",
                    len(adzuna_candidates),
                )
                candidate_sources.append(adzuna_candidates)
            if self.reed:
                reed_candidates = self.reed.search(
                    self.ai.search_config,
                    results_per_query=self.settings.reed_results_per_query,
                    max_candidates=self.settings.reed_max_candidates,
                )
                logger.info("Reed returned %s candidates", len(reed_candidates))
                candidate_sources.append(reed_candidates)
            if candidate_sources and remaining > 0:
                candidates = _interleave_sources(*candidate_sources)
                reviewed_jobs = self.ai.review_candidates(candidates, remaining)
                logger.info("%s aggregated candidates passed AI review", len(reviewed_jobs))
            raw_jobs = _deduplicate_jobs(web_jobs + reviewed_jobs)[
                : self.settings.max_discovered_jobs
            ]
            discovered = len(raw_jobs)
            for raw in raw_jobs:
                employer = str(raw.get("employer", ""))
                prepared = prepare_job(
                    raw,
                    sponsor_match=matcher.match(employer),
                    min_fit_score=self.settings.min_fit_score,
                )
                self.db.upsert_job(run_id, prepared)

            jobs = self.db.unemailed_digest_jobs(
                possible_limit=self.settings.possible_email_limit
            )
            qualified = len(jobs)
            if jobs and not self.settings.dry_run_email:
                self.emailer.send_digest(jobs)
                self.db.mark_emailed([int(job["id"]) for job in jobs])
                emailed = len(jobs)
            self.db.finish_run(
                run_id,
                status="completed",
                discovered=discovered,
                qualified=qualified,
                emailed=emailed,
            )
            return RunResult(discovered, qualified, emailed)
        except Exception as exc:
            logger.exception("Job search run failed")
            self.db.finish_run(
                run_id,
                status="failed",
                discovered=discovered,
                qualified=qualified,
                emailed=emailed,
                error=str(exc)[:2000],
            )
            raise

    def send_pending(self) -> int:
        self.db.initialise()
        jobs = self.db.unemailed_digest_jobs(
            possible_limit=self.settings.possible_email_limit
        )
        if not jobs:
            return 0
        if self.settings.dry_run_email:
            logger.info("Dry run: %s pending jobs were not emailed", len(jobs))
            return 0
        self.emailer.send_digest(jobs)
        self.db.mark_emailed([int(job["id"]) for job in jobs])
        return len(jobs)


def _deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen_urls: set[str] = set()
    seen_roles: set[tuple[str, str]] = set()
    for job in jobs:
        url = str(job.get("vacancy_url") or "").strip().casefold().split("?", 1)[0]
        title = " ".join(
            _NON_WORD.sub(" ", str(job.get("title") or "").casefold()).split()
        )
        employer = normalise_company_name(str(job.get("employer") or ""))
        role = (title, employer)
        if (url and url in seen_urls) or (all(role) and role in seen_roles):
            continue
        if url:
            seen_urls.add(url)
        if all(role):
            seen_roles.add(role)
        unique.append(job)
    return unique


def _interleave_sources(*sources: list[dict]) -> list[dict]:
    combined: list[dict] = []
    longest = max((len(source) for source in sources), default=0)
    for index in range(longest):
        for source in sources:
            if index < len(source):
                combined.append(source[index])
    return combined
