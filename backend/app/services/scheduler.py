from datetime import datetime
from typing import Optional

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from app.core.config import get_settings


class SchedulerService:
    def __init__(self) -> None:
        self.settings = get_settings()
        jobstores = {
            "default": SQLAlchemyJobStore(url=self.settings.database_url_sync)
        }
        self.scheduler = AsyncIOScheduler(
            timezone=self.settings.timezone,
            jobstores=jobstores,
        )

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        if not self.scheduler.running:
            self.scheduler.start()

    def add_job(self, job_id: str, run_at: datetime, func, *args, **kwargs) -> None:
        trigger = DateTrigger(run_date=run_at, timezone=self.settings.timezone)
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            args=args,
            kwargs=kwargs,
            replace_existing=True,
        )

    def remove_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            return


_scheduler_instance: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = SchedulerService()
    return _scheduler_instance
