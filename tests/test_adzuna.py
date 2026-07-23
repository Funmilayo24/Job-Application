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
