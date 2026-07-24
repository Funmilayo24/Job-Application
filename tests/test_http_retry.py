from __future__ import annotations

import httpx
import pytest

from app.http_retry import SourceRequestError, request_with_retries


def test_request_retries_temporary_status_and_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 3 else 200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = request_with_retries(
        client,
        "https://jobs.example.test/search",
        source_name="Example",
        attempts=3,
        backoff_seconds=0,
        params={"api_key": "secret-value"},
    )

    assert response.status_code == 200
    assert attempts == 3


def test_request_error_does_not_expose_url_credentials() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )

    with pytest.raises(SourceRequestError) as error:
        request_with_retries(
            client,
            "https://jobs.example.test/search",
            source_name="Example",
            attempts=2,
            backoff_seconds=0,
            params={"api_key": "secret-value"},
        )

    assert str(error.value) == "Example request failed with HTTP 503 after 2 attempts"
    assert "secret-value" not in str(error.value)
    assert "jobs.example.test" not in str(error.value)
