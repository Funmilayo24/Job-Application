from __future__ import annotations

import logging
from typing import Any

import httpx

from app.http_retry import SourceRequestError, request_with_retries

logger = logging.getLogger(__name__)


class AdzunaClient:
    endpoint = "https://api.adzuna.com/v1/api/jobs/gb/search/1"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        *,
        client: httpx.Client | None = None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.app_id = app_id
        self.app_key = app_key
        self.client = client
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    def search(
        self,
        search_config: dict[str, Any],
        *,
        results_per_query: int = 10,
        max_candidates: int = 40,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for track in search_config["career_tracks"]:
                for query in track.get("adzuna_queries", []):
                    try:
                        response = request_with_retries(
                            client,
                            self.endpoint,
                            source_name="Adzuna",
                            attempts=self.retry_attempts,
                            backoff_seconds=self.retry_backoff_seconds,
                            params={
                                "app_id": self.app_id,
                                "app_key": self.app_key,
                                "results_per_page": results_per_query,
                                "what": query,
                                "max_days_old": 14,
                                "sort_by": "date",
                                "content-type": "application/json",
                            },
                        )
                    except SourceRequestError as exc:
                        logger.warning("%s; skipping query %r", exc, query)
                        continue
                    for advert in response.json().get("results", []):
                        identifier = str(advert.get("id") or advert.get("redirect_url") or "")
                        if not identifier or identifier in seen:
                            continue
                        seen.add(identifier)
                        candidates.append(self._candidate(advert, track["name"]))
                        if len(candidates) >= max_candidates:
                            return candidates
        finally:
            if owns_client:
                client.close()
        return candidates

    @staticmethod
    def _candidate(advert: dict[str, Any], track: str) -> dict[str, Any]:
        company = advert.get("company") or {}
        location = advert.get("location") or {}
        minimum = advert.get("salary_min")
        maximum = advert.get("salary_max")
        if minimum is not None and maximum is not None:
            salary_text = f"£{minimum:,.0f}–£{maximum:,.0f}"
        elif minimum is not None:
            salary_text = f"From £{minimum:,.0f}"
        elif maximum is not None:
            salary_text = f"Up to £{maximum:,.0f}"
        else:
            salary_text = "Not stated"
        return {
            "source_name": "Adzuna",
            "source_id": str(advert.get("id") or ""),
            "title": str(advert.get("title") or ""),
            "employer": str(company.get("display_name") or ""),
            "location": str(location.get("display_name") or ""),
            "salary_text": salary_text,
            "vacancy_url": str(advert.get("redirect_url") or ""),
            "posted_at": str(advert.get("created") or ""),
            "description": str(advert.get("description") or ""),
            "suggested_track": track,
        }
