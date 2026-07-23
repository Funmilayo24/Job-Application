from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

_CLOSED_PATTERNS = (
    re.compile(r"\bthis (?:job|vacancy|position) (?:is|has been) (?:now )?closed\b"),
    re.compile(r"\bapplications (?:are|have) (?:now )?closed\b"),
    re.compile(r"\b(?:job|vacancy|position) (?:is )?no longer available\b"),
    re.compile(r"\bthis (?:job|vacancy) has expired\b"),
    re.compile(r"\bposition has been filled\b"),
)
_HTML_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_MAX_PAGE_BYTES = 96_000


@dataclass(frozen=True)
class VacancyValidation:
    link_status: str
    expiry_status: str
    resolved_vacancy_url: str | None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def preserve_confirmed_closure(
    job: dict[str, Any],
    validation: VacancyValidation,
) -> VacancyValidation:
    link_status = validation.link_status
    expiry_status = validation.expiry_status
    if job.get("link_status") == "broken" and link_status != "active":
        link_status = "broken"
    if job.get("expiry_status") == "expired" and expiry_status != "open":
        expiry_status = "expired"
    return VacancyValidation(
        link_status=link_status,
        expiry_status=expiry_status,
        resolved_vacancy_url=(
            validation.resolved_vacancy_url or job.get("resolved_vacancy_url")
        ),
    )


def parse_closing_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalised = re.sub(
        r"(?i)^(?:closing date|closes|closing|apply by)\s*[:\-]?\s*",
        "",
        text,
    ).strip()
    iso_candidate = normalised.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(normalised)
    except ValueError:
        pass
    for pattern in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(normalised, pattern).date()
        except ValueError:
            continue
    return None


def page_says_closed(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore").casefold()
    text = _SPACE.sub(" ", unescape(_HTML_TAG.sub(" ", text)))
    return any(pattern.search(text) for pattern in _CLOSED_PATTERNS)


class VacancyValidator:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        today: date | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(12, connect=8),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; SponsorPath/1.0; "
                    "+https://github.com/Funmilayo24/Job-Application)"
                )
            },
        )
        self._owns_client = client is None
        self.today = today or datetime.now(UTC).astimezone(ZoneInfo("Europe/London")).date()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> VacancyValidator:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def validate(self, job: dict[str, Any]) -> VacancyValidation:
        closing_date = parse_closing_date(job.get("closing_at"))
        if closing_date and closing_date < self.today:
            return VacancyValidation(
                link_status="unverified",
                expiry_status="expired",
                resolved_vacancy_url=None,
            )

        expiry_status = "open" if closing_date else "unknown"
        url = str(job.get("vacancy_url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return VacancyValidation(
                link_status="broken",
                expiry_status=expiry_status,
                resolved_vacancy_url=None,
            )

        try:
            with self.client.stream("GET", url) as response:
                resolved_url = str(response.url)
                if response.status_code in {404, 410}:
                    return VacancyValidation("broken", expiry_status, resolved_url)
                if not 200 <= response.status_code < 400:
                    return VacancyValidation("unverified", expiry_status, resolved_url)
                content = bytearray()
                for chunk in response.iter_bytes():
                    remaining = _MAX_PAGE_BYTES - len(content)
                    if remaining <= 0:
                        break
                    content.extend(chunk[:remaining])
                    if len(content) >= _MAX_PAGE_BYTES:
                        break
                if page_says_closed(bytes(content)):
                    expiry_status = "expired"
                return VacancyValidation("active", expiry_status, resolved_url)
        except httpx.HTTPError:
            return VacancyValidation("unverified", expiry_status, None)

    def validate_many(
        self,
        jobs: list[dict[str, Any]],
        *,
        max_workers: int = 8,
    ) -> list[VacancyValidation]:
        if not jobs:
            return []
        workers = max(1, min(max_workers, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self.validate, jobs))
