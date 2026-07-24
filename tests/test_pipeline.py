from types import SimpleNamespace

from app.pipeline import (
    JobPipeline,
    _deduplicate_jobs,
    _interleave_sources,
    _safe_provider_search,
)


def test_deduplicate_jobs_across_sources_by_role_and_employer() -> None:
    jobs = [
        {
            "title": "Customer Success Manager",
            "employer": "Example Limited",
            "vacancy_url": "https://employer.test/jobs/1",
        },
        {
            "title": "Customer Success Manager",
            "employer": "Example Ltd",
            "vacancy_url": "https://adzuna.test/redirect/99",
        },
    ]
    assert len(_deduplicate_jobs(jobs)) == 1


def test_interleave_sources_gives_each_board_equal_priority() -> None:
    assert _interleave_sources(
        [{"source": "a1"}, {"source": "a2"}],
        [{"source": "b1"}],
    ) == [{"source": "a1"}, {"source": "b1"}, {"source": "a2"}]


def test_send_pending_emails_and_marks_jobs_without_searching() -> None:
    class FakeDatabase:
        marked: list[int] = []

        def initialise(self) -> None:
            pass

        def unemailed_digest_jobs(self, *, possible_limit: int) -> list[dict]:
            assert possible_limit == 15
            return [{"id": 7}, {"id": 8}]

        def mark_emailed(self, job_ids: list[int]) -> None:
            self.marked = job_ids

    class FakeEmailer:
        sent: list[dict] = []

        def send_digest(self, jobs: list[dict]) -> str:
            self.sent = jobs
            return "email-id"

    pipeline = JobPipeline.__new__(JobPipeline)
    pipeline.settings = SimpleNamespace(possible_email_limit=15, dry_run_email=False)
    pipeline.db = FakeDatabase()
    pipeline.emailer = FakeEmailer()

    assert pipeline.send_pending() == 2
    assert pipeline.db.marked == [7, 8]
    assert pipeline.emailer.sent == [{"id": 7}, {"id": 8}]


def test_provider_failure_isolated_without_logging_exception_message(caplog) -> None:
    def failed_search() -> list[dict]:
        raise RuntimeError("request contained secret-api-key")

    assert _safe_provider_search("Example", failed_search) == []
    assert "continuing with other sources (RuntimeError)" in caplog.text
    assert "secret-api-key" not in caplog.text
