from dataclasses import dataclass

from app.job_logic import canonical_key, prepare_job, qualification_status
from app.openai_service import load_search_config


@dataclass
class Match:
    matched: bool
    register_name: str | None
    score: int


def base_job() -> dict:
    return {
        "title": "Customer Success Manager",
        "employer": "Example Ltd",
        "location": "London",
        "salary_text": "£50,000",
        "source_name": "Employer",
        "vacancy_url": "https://example.com/jobs/123?utm_source=test",
        "posted_at": "2026-07-23",
        "closing_at": "",
        "summary": "Customer success role",
        "career_track": "customer_success",
        "fit_score": 85,
        "fit_reasons": ["Relevant customer success experience"],
        "missing_requirements": [],
        "sponsorship_claim": "Skilled Worker sponsorship available",
        "sponsorship_evidence": "The vacancy explicitly offers sponsorship.",
        "sponsorship_evidence_url": "https://example.com/jobs/123",
        "explicit_sponsorship": True,
        "salary_rule_status": "meets",
        "salary_rule_reason": "Advertised salary meets the applicable requirement.",
    }


def test_canonical_key_ignores_tracking_query() -> None:
    one = base_job()
    two = {**one, "vacancy_url": "https://example.com/jobs/123?ref=linkedin"}
    assert canonical_key(one) == canonical_key(two)


def test_qualified_requires_all_gates() -> None:
    job = base_job()
    job["sponsor_register_match"] = True
    assert qualification_status(job, 65) == "qualified_confirmed"


def test_unclear_salary_is_not_qualified() -> None:
    job = base_job()
    job["sponsor_register_match"] = True
    job["salary_rule_status"] = "unclear"
    assert qualification_status(job, 65) == "review_salary_unclear"


def test_prepare_job_adds_sponsor_match_and_status() -> None:
    prepared = prepare_job(
        base_job(),
        sponsor_match=Match(True, "Example Limited", 100),
        min_fit_score=65,
    )
    assert prepared["sponsor_register_match"] is True
    assert prepared["qualification_status"] == "qualified_confirmed"
    assert prepared["sponsorship_tier"] == "confirmed"


def test_silent_vacancy_at_licensed_employer_is_possible() -> None:
    job = base_job()
    job["sponsor_register_match"] = True
    job["explicit_sponsorship"] = False
    assert qualification_status(job, 65) == "qualified_possible"


def test_explicit_sponsorship_exclusion_is_rejected() -> None:
    job = base_job()
    job["sponsor_register_match"] = True
    job["sponsorship_exclusion"] = True
    assert qualification_status(job, 65) == "rejected_sponsorship_excluded"


def test_expired_or_broken_vacancy_is_rejected() -> None:
    job = base_job()
    job["sponsor_register_match"] = True
    job["expiry_status"] = "expired"
    assert qualification_status(job, 65) == "rejected_expired"

    job["expiry_status"] = "open"
    job["link_status"] = "broken"
    assert qualification_status(job, 65) == "rejected_broken_link"


def test_search_titles_are_semantic_seeds() -> None:
    config = load_search_config()
    titles = {
        title
        for track in config["career_tracks"]
        for title in track["titles"]
    }
    assert config["matching_policy"]["mode"] == "semantic"
    assert "Head of Customer Service" in titles
    assert "Payments Product Manager" in titles
    assert "Project Support Officer" in titles


def test_official_public_sector_sources_are_prioritised() -> None:
    config = load_search_config()
    sources = {
        source["name"]: source["domain"]
        for source in config["priority_sources"]
    }
    assert sources == {
        "NHS Jobs": "jobs.nhs.uk",
        "Civil Service Jobs": "civilservicejobs.service.gov.uk",
    }
