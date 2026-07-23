from __future__ import annotations

import base64

import httpx

from app.reed import ReedClient


def test_reed_search_authenticates_maps_and_deduplicates_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(b"secret:").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert request.url.params["postedByDirectEmployer"] == "true"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "jobId": 123,
                        "jobTitle": "Product Manager",
                        "employerName": "Example Ltd",
                        "locationName": "London",
                        "minimumSalary": 50000,
                        "maximumSalary": 60000,
                        "jobDescription": "Own a payments product roadmap.",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = ReedClient("secret", client=client)
    config = {
        "career_tracks": [
            {
                "name": "product_payments_and_delivery",
                "reed_queries": ["product manager", "payments product manager"],
            }
        ]
    }
    jobs = source.search(config)
    assert len(jobs) == 1
    assert jobs[0]["source_name"] == "Reed"
    assert jobs[0]["salary_text"] == "£50,000–£60,000"
