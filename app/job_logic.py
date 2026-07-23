from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SPACE = re.compile(r"\s+")


def canonicalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.casefold() or "https"
    host = parts.netloc.casefold()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, host, path, "", ""))


def canonical_key(job: dict[str, Any]) -> str:
    url = canonicalise_url(str(job.get("vacancy_url", "")))
    if url and url != "https:":
        source = url
    else:
        source = "|".join(
            _SPACE.sub(" ", str(job.get(name, "")).strip().casefold())
            for name in ("title", "employer", "location")
        )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def qualification_status(job: dict[str, Any], min_fit_score: int) -> str:
    if job.get("sponsorship_exclusion"):
        return "rejected_sponsorship_excluded"
    if not job.get("sponsor_register_match"):
        return "review_sponsor_not_matched"
    if job.get("salary_rule_status") == "fails":
        return "rejected_salary"
    if int(job.get("fit_score", 0)) < min_fit_score:
        return "rejected_low_fit"
    if job.get("explicit_sponsorship"):
        if job.get("salary_rule_status") != "meets":
            return "review_salary_unclear"
        return "qualified_confirmed"
    return "qualified_possible"


def prepare_job(
    raw: dict[str, Any],
    *,
    sponsor_match: Any,
    min_fit_score: int,
) -> dict[str, Any]:
    job = {
        "title": str(raw.get("title", "")).strip(),
        "employer": str(raw.get("employer", "")).strip(),
        "location": str(raw.get("location", "")).strip() or None,
        "salary_text": str(raw.get("salary_text", "")).strip() or None,
        "source_name": str(raw.get("source_name", "")).strip() or None,
        "vacancy_url": canonicalise_url(str(raw.get("vacancy_url", ""))),
        "posted_at": str(raw.get("posted_at", "")).strip() or None,
        "closing_at": str(raw.get("closing_at", "")).strip() or None,
        "summary": str(raw.get("summary", "")).strip() or None,
        "career_track": str(raw.get("career_track", "")).strip() or None,
        "fit_score": max(0, min(100, int(raw.get("fit_score", 0)))),
        "fit_reasons": list(raw.get("fit_reasons") or []),
        "missing_requirements": list(raw.get("missing_requirements") or []),
        "sponsorship_claim": str(raw.get("sponsorship_claim", "")).strip() or None,
        "sponsorship_evidence": str(raw.get("sponsorship_evidence", "")).strip() or None,
        "sponsorship_evidence_url": str(raw.get("sponsorship_evidence_url", "")).strip()
        or None,
        "explicit_sponsorship": bool(raw.get("explicit_sponsorship")),
        "sponsorship_exclusion": bool(raw.get("sponsorship_exclusion")),
        "sponsorship_exclusion_evidence": str(
            raw.get("sponsorship_exclusion_evidence", "")
        ).strip()
        or None,
        "sponsor_register_match": bool(sponsor_match.matched),
        "sponsor_register_name": sponsor_match.register_name,
        "sponsor_match_score": sponsor_match.score,
        "salary_rule_status": str(raw.get("salary_rule_status", "unclear")).strip(),
        "salary_rule_reason": str(raw.get("salary_rule_reason", "")).strip() or None,
    }
    job["canonical_key"] = canonical_key(job)
    job["qualification_status"] = qualification_status(job, min_fit_score)
    if job["qualification_status"] == "qualified_confirmed":
        job["sponsorship_tier"] = "confirmed"
    elif job["qualification_status"] == "qualified_possible":
        job["sponsorship_tier"] = "possible"
    else:
        job["sponsorship_tier"] = "rejected"
    return job
