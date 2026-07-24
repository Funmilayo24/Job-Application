from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class SourceRequestError(RuntimeError):
    """A credential-safe error raised after a job-board request fails."""


def request_with_retries(
    client: httpx.Client,
    url: str,
    *,
    source_name: str,
    attempts: int = 3,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    **request_kwargs: Any,
) -> httpx.Response:
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            retryable = status_code in RETRYABLE_STATUS_CODES
            if retryable and attempt < attempts:
                sleep(backoff_seconds * (2 ** (attempt - 1)))
                continue
            attempt_text = f" after {attempt} attempts" if attempt > 1 else ""
            raise SourceRequestError(
                f"{source_name} request failed with HTTP {status_code}{attempt_text}"
            ) from None
        except httpx.RequestError:
            if attempt < attempts:
                sleep(backoff_seconds * (2 ** (attempt - 1)))
                continue
            raise SourceRequestError(
                f"{source_name} request failed because of a network error "
                f"after {attempt} attempts"
            ) from None

    raise AssertionError("request retry loop exited unexpectedly")
