from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from rapidfuzz import fuzz, process

REGISTER_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
_COMPANY_SUFFIXES = re.compile(
    r"\b(limited|ltd|plc|llp|incorporated|inc|company|co|uk|the)\b", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_company_name(value: str) -> str:
    value = html.unescape(value).casefold()
    value = value.replace("&", " and ")
    value = _COMPANY_SUFFIXES.sub(" ", value)
    return " ".join(_NON_ALNUM.sub(" ", value).split())


def _field(row: dict[str, str], *names: str) -> str:
    lowered = {key.casefold().strip(): (value or "").strip() for key, value in row.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return ""


def discover_csv_url(page_html: str) -> str:
    candidates = re.findall(r'href=["\']([^"\']+)["\']', page_html, flags=re.IGNORECASE)
    for candidate in candidates:
        candidate = html.unescape(candidate)
        lowered = candidate.casefold()
        if lowered.endswith(".csv") or "/csv-preview/" in lowered:
            return urljoin(REGISTER_PAGE, candidate)
    raise RuntimeError("Could not find the sponsor-register CSV link on GOV.UK")


def download_sponsors(client: httpx.Client | None = None) -> list[dict[str, str]]:
    owns_client = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        page = client.get(REGISTER_PAGE)
        page.raise_for_status()
        csv_url = discover_csv_url(page.text)
        response = client.get(csv_url)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
    finally:
        if owns_client:
            client.close()

    rows: list[dict[str, str]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        name = _field(raw, "Organisation Name", "Organisation")
        if not name:
            continue
        rows.append(
            {
                "organisation_name": name,
                "normalised_name": normalise_company_name(name),
                "town_city": _field(raw, "Town/City", "Town City"),
                "county": _field(raw, "County"),
                "type_and_rating": _field(raw, "Type & Rating", "Type and Rating"),
                "route": _field(raw, "Route"),
            }
        )
    if not rows:
        raise RuntimeError("The GOV.UK sponsor register returned no usable rows")
    return rows


@dataclass(frozen=True)
class SponsorMatch:
    matched: bool
    register_name: str | None
    score: int


class SponsorMatcher:
    def __init__(self, sponsors: list[dict[str, str]], threshold: int = 92) -> None:
        self.threshold = threshold
        self._by_normalised: dict[str, list[str]] = {}
        for sponsor in sponsors:
            normalised = sponsor["normalised_name"]
            self._by_normalised.setdefault(normalised, []).append(sponsor["organisation_name"])
        self._choices = list(self._by_normalised)

    def match(self, employer: str) -> SponsorMatch:
        query = normalise_company_name(employer)
        if not query or not self._choices:
            return SponsorMatch(False, None, 0)
        if query in self._by_normalised:
            return SponsorMatch(True, self._by_normalised[query][0], 100)
        if len(query) >= 5:
            contained = [
                choice
                for choice in self._choices
                if query in choice or choice in query
            ]
            if len(contained) == 1:
                normalised = contained[0]
                return SponsorMatch(True, self._by_normalised[normalised][0], 96)
        result = process.extractOne(query, self._choices, scorer=fuzz.WRatio)
        if not result:
            return SponsorMatch(False, None, 0)
        normalised, score, _ = result
        rounded = int(round(score))
        return SponsorMatch(
            rounded >= self.threshold,
            self._by_normalised[normalised][0] if rounded >= self.threshold else None,
            rounded,
        )
