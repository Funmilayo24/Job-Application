from __future__ import annotations

import httpx

from app.adzuna import AdzunaClient


def test_adzuna_search_maps_and_deduplicates_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["app_id"] == "id"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "123",
                        "title": "Customer Success Manager",
                        "company": {"display_name": "Example Ltd"},
                        "location": {"display_name": "London"},
                        "salary_min": 50000,
                        "salary_max": 60000,
                        "redirect_url": "https://example.test/123",
                        "created": "2026-07-23T08:00:00Z",
                        "description": "Lead customer onboarding.",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = AdzunaClient("id", "key", client=client)
    config = {
        "career_tracks": [
            {
                "name": "customer_success_and_operations",
                "adzuna_queries": ["customer success", "customer operations"],
            }
        ]
    }
    jobs = source.search(config)
    assert len(jobs) == 1
    assert jobs[0]["source_name"] == "Adzuna"
    assert jobs[0]["salary_text"] == "£50,000–£60,000"


def test_adzuna_retries_failed_query_and_preserves_other_results(caplog) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["what"]
        calls.append(query)
        if query == "temporary failure":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "456",
                        "title": "Operations Manager",
                        "company": {"display_name": "Example Ltd"},
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = AdzunaClient(
        "id",
        "credential-that-must-not-be-logged",
        client=client,
        retry_attempts=3,
        retry_backoff_seconds=0,
    )
    jobs = source.search(
        {
            "career_tracks": [
                {
                    "name": "operations",
                    "adzuna_queries": ["temporary failure", "operations manager"],
                }
            ]
        }
    )

    assert calls == [
        "temporary failure",
        "temporary failure",
        "temporary failure",
        "operations manager",
    ]
    assert [job["source_id"] for job in jobs] == ["456"]
    assert "Adzuna request failed with HTTP 503 after 3 attempts" in caplog.text
    assert "credential-that-must-not-be-logged" not in caplog.text
