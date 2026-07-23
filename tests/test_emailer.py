import pytest

from app.emailer import parse_recipients, render_digest


def test_parse_multiple_recipients() -> None:
    assert parse_recipients("one@example.com, two@example.com") == [
        "one@example.com",
        "two@example.com",
    ]


def test_parse_recipients_rejects_empty_value() -> None:
    with pytest.raises(ValueError):
        parse_recipients(" , ")


def test_digest_escapes_untrusted_job_text() -> None:
    html = render_digest(
        [
            {
                "title": "<script>alert(1)</script>",
                "employer": "Example",
                "location": "London",
                "salary_text": "£50,000",
                "fit_score": 80,
                "sponsorship_evidence": "Sponsorship available",
                "sponsor_register_name": "Example Ltd",
                "sponsorship_tier": "confirmed",
                "fit_reasons": ["Relevant experience"],
                "vacancy_url": "https://example.com/job",
            }
        ]
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_digest_separates_possible_sponsorship_jobs() -> None:
    rendered = render_digest(
        [
            {
                "title": "Operations Manager",
                "employer": "Licensed Employer",
                "vacancy_url": "https://example.com/job",
                "fit_score": 82,
                "sponsor_register_name": "Licensed Employer Ltd",
                "sponsorship_tier": "possible",
            }
        ]
    )
    assert "Sponsorship possible — confirm before applying" in rendered
    assert "Confirmation required" in rendered
    assert "require Skilled Worker" in rendered
