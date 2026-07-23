from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.db import evaluate_manual_search_limits
from app.web import next_scheduled_search
from app.worker import process_manual_search


def test_manual_search_is_available_without_recent_history() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=UTC)
    state = evaluate_manual_search_limits(
        now=now,
        latest_run_at=None,
        latest_manual_at=None,
        active_request=None,
    )
    assert state["can_request"] is True
    assert state["next_allowed_at"] is None


def test_manual_search_uses_the_later_of_both_cost_limits() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=UTC)
    state = evaluate_manual_search_limits(
        now=now,
        latest_run_at=now - timedelta(hours=5),
        latest_manual_at=now - timedelta(hours=20),
        active_request=None,
    )
    assert state["can_request"] is False
    assert state["next_allowed_at"] == now + timedelta(hours=4)
    assert state["reason"] == "Only one manual search is allowed every 24 hours."


def test_active_request_blocks_another_manual_search() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=UTC)
    state = evaluate_manual_search_limits(
        now=now,
        latest_run_at=None,
        latest_manual_at=None,
        active_request={"id": 4, "status": "running"},
    )
    assert state["can_request"] is False
    assert state["reason"] == "A manual search is running."


def test_next_scheduled_search_uses_london_time() -> None:
    settings = SimpleNamespace(timezone="Europe/London", search_hours=(8, 18))
    now = datetime(2026, 7, 23, 8, tzinfo=UTC)  # 09:00 in London
    next_run = next_scheduled_search(settings, now=now)
    assert next_run.hour == 18
    assert next_run.tzname() == "BST"


def test_worker_completes_a_queued_manual_search() -> None:
    class FakeDatabase:
        completed: tuple[int, str] | None = None

        def claim_manual_search(self) -> dict:
            return {"id": 12, "requested_by": "sister"}

        def finish_manual_search(
            self,
            request_id: int,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            assert error is None
            self.completed = (request_id, status)

    class FakePipeline:
        db = FakeDatabase()
        ran = False

        def run(self) -> None:
            self.ran = True

    pipeline = FakePipeline()
    process_manual_search(pipeline)  # type: ignore[arg-type]
    assert pipeline.ran is True
    assert pipeline.db.completed == (12, "completed")
