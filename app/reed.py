from __future__ import annotations

import logging
from typing import Any

import httpx

from app.http_retry import SourceRequestError, request_with_retries

logger = logging.getLogger(__name__)


class ReedClient:
    endpoint = "https://www.reed.co.uk/api/1.0/search"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.api_key = api_key
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
                for query in track.get("reed_queries", track.get("adzuna_queries", [])):
                    try:
                        response = request_with_retries(
                            client,
                            self.endpoint,
                            source_name="Reed",
                            attempts=self.retry_attempts,
                            backoff_seconds=self.retry_backoff_seconds,
                            params={
                                "keywords": query,
                                "permanent": "true",
                                "fullTime": "true",
                                "postedByDirectEmployer": "true",
                                "resultsToTake": results_per_query,
                            },
                            auth=httpx.BasicAuth(self.api_key, ""),
                        )
                    except SourceRequestError as exc:
                        logger.warning("%s; skipping query %r", exc, query)
                        continue
                    for advert in response.json().get("results", []):
                        identifier = str(advert.get("jobId") or "")
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
        identifier = str(advert.get("jobId") or "")
        minimum = advert.get("minimumSalary")
        maximum = advert.get("maximumSalary")
        if minimum is not None and maximum is not None:
            salary_text = f"£{minimum:,.0f}–£{maximum:,.0f}"
        elif minimum is not None:
            salary_text = f"From £{minimum:,.0f}"
        elif maximum is not None:
            salary_text = f"Up to £{maximum:,.0f}"
        else:
            salary_text = "Not stated"
        return {
            "source_name": "Reed",
            "source_id": identifier,
            "title": str(advert.get("jobTitle") or ""),
            "employer": str(advert.get("employerName") or ""),
            "location": str(advert.get("locationName") or ""),
            "salary_text": salary_text,
            "vacancy_url": str(
                advert.get("jobUrl") or f"https://www.reed.co.uk/jobs/{identifier}"
            ),
            "posted_at": str(advert.get("date") or ""),
            "description": str(advert.get("jobDescription") or advert.get("description") or ""),
            "suggested_track": track,
        }
