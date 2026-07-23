from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.pipeline import JobPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


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
    logger.info(
        "Scheduler ready: hours=%s timezone=%s",
        settings.search_hours,
        settings.timezone,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
