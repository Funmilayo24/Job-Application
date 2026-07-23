from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.pipeline import JobPipeline, SearchAlreadyRunningError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def process_manual_search(pipeline: JobPipeline) -> None:
    request = pipeline.db.claim_manual_search()
    if not request:
        return
    request_id = int(request["id"])
    logger.info(
        "Starting manual search request id=%s requested_by=%s",
        request_id,
        request["requested_by"],
    )
    try:
        pipeline.run()
    except SearchAlreadyRunningError:
        pipeline.db.requeue_manual_search(request_id)
        logger.info("Manual request id=%s requeued because another search is running", request_id)
    except Exception as exc:
        pipeline.db.finish_manual_search(
            request_id,
            status="failed",
            error=str(exc)[:2000],
        )
        logger.exception("Manual search request id=%s failed", request_id)
    else:
        pipeline.db.finish_manual_search(request_id, status="completed")
        logger.info("Manual search request id=%s completed", request_id)


def main() -> None:
    settings = Settings.from_env()
    pipeline = JobPipeline(settings)
    pipeline.db.initialise()

    if settings.run_on_startup:
        pipeline.run()

    scheduler = BlockingScheduler(timezone=settings.timezone)
    scheduler.add_job(
        pipeline.run,
        CronTrigger(hour=",".join(map(str, settings.search_hours)), minute=0),
        id="uk-sponsorship-search",
        name="UK sponsorship job search",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        process_manual_search,
        IntervalTrigger(seconds=60),
        args=[pipeline],
        id="manual-search-queue",
        name="Manual search request queue",
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Scheduler ready: hours=%s timezone=%s",
        settings.search_hours,
        settings.timezone,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
