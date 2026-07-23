from datetime import date

import httpx

from app.vacancy_validator import (
    VacancyValidation,
    VacancyValidator,
    page_says_closed,
    parse_closing_date,
    preserve_confirmed_closure,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_parse_closing_date_supports_common_uk_formats() -> None:
    assert parse_closing_date("Closing date: 31/07/2026") == date(2026, 7, 31)
    assert parse_closing_date("31 July 2026") == date(2026, 7, 31)
    assert parse_closing_date("2026-07-31T23:59:00+01:00") == date(2026, 7, 31)
    assert parse_closing_date("Not stated") is None


def test_expired_date_is_rejected_without_requesting_the_link() -> None:
    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("An expired vacancy should not make an HTTP request")

    with _client(unexpected_request) as client:
        validation = VacancyValidator(client=client, today=date(2026, 7, 23)).validate(
            {
                "closing_at": "22 July 2026",
                "vacancy_url": "https://example.test/job",
            }
        )
    assert validation.expiry_status == "expired"
    assert validation.link_status == "unverified"


def test_404_is_broken_but_access_blocks_are_unverified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = 404 if request.url.path == "/missing" else 403
        return httpx.Response(status, request=request)

    with _client(handler) as client:
        validator = VacancyValidator(client=client, today=date(2026, 7, 23))
        missing = validator.validate({"vacancy_url": "https://example.test/missing"})
        blocked = validator.validate({"vacancy_url": "https://example.test/blocked"})
    assert missing.link_status == "broken"
    assert blocked.link_status == "unverified"


def test_live_page_and_soft_closed_page_are_distinguished() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "<h1>This vacancy is no longer available</h1>"
            if request.url.path == "/closed"
            else "<h1>Customer Success Manager</h1><button>Apply now</button>"
        )
        return httpx.Response(200, html=body, request=request)

    with _client(handler) as client:
        validator = VacancyValidator(client=client, today=date(2026, 7, 23))
        live = validator.validate({"vacancy_url": "https://example.test/live"})
        closed = validator.validate({"vacancy_url": "https://example.test/closed"})
    assert live.link_status == "active"
    assert live.expiry_status == "unknown"
    assert closed.link_status == "active"
    assert closed.expiry_status == "expired"


def test_page_says_closed_ignores_unrelated_open_copy() -> None:
    assert page_says_closed(b"<p>Applications are now closed.</p>") is True
    assert page_says_closed(b"<p>Applications are open until Friday.</p>") is False


def test_inconclusive_recheck_does_not_reopen_confirmed_broken_job() -> None:
    validation = preserve_confirmed_closure(
        {"link_status": "broken", "resolved_vacancy_url": "https://example.test/job"},
        VacancyValidation("unverified", "unknown", None),
    )
    assert validation.link_status == "broken"
    assert validation.resolved_vacancy_url == "https://example.test/job"
