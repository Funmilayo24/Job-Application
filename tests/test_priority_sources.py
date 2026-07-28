from app.openai_service import OpenAIJobService


def _job(url: str, source_name: str = "Incorrect label") -> dict:
    return {
        "title": "Project Manager",
        "employer": "Example employer",
        "vacancy_url": url,
        "source_name": source_name,
    }


def test_priority_sources_get_independent_track_searches_and_reserved_capacity() -> None:
    service = OpenAIJobService.__new__(OpenAIJobService)
    service.profile = {"name": "Candidate"}
    service.search_config = {
        "priority_sources": [
            {"name": "NHS Jobs", "domain": "jobs.nhs.uk", "search_url": "nhs"},
            {
                "name": "Civil Service Jobs",
                "domain": "civilservicejobs.service.gov.uk",
                "search_url": "cs",
            },
        ],
        "career_tracks": [
            {"name": "customer", "titles": ["Operations Manager"]},
            {"name": "delivery", "titles": ["Project Manager"]},
        ],
    }
    calls: list[str] = []

    def fake_request(prompt: str, *, use_web: bool) -> list[dict]:
        assert use_web is True
        calls.append(prompt)
        if "NHS Jobs website" in prompt:
            number = sum("NHS Jobs website" in call for call in calls)
            return [
                _job(
                    f"https://www.jobs.nhs.uk/candidate/jobadvert/C{number}",
                ),
                _job("https://aggregator.example/jobs/not-official"),
            ]
        number = sum("Civil Service Jobs website" in call for call in calls)
        return [
            _job(
                "https://www.civilservicejobs.service.gov.uk/csr/jobs.cgi?"
                f"jcode={number}"
            )
        ]

    service._request_jobs = fake_request  # type: ignore[method-assign]
    jobs = service.discover_priority_source_jobs(4)

    assert len(calls) == 4
    assert len(jobs) == 4
    assert [job["source_name"] for job in jobs].count("NHS Jobs") == 2
    assert [job["source_name"] for job in jobs].count("Civil Service Jobs") == 2
    assert all("aggregator.example" not in job["vacancy_url"] for job in jobs)


def test_general_discovery_does_not_search_when_capacity_is_zero() -> None:
    service = OpenAIJobService.__new__(OpenAIJobService)
    assert service.discover_jobs(0) == []
