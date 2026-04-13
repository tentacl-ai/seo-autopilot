"""
Scheduler – APScheduler Integration

Runs periodic audits based on cron expressions.
Multi-tenant ready: each project can have its own schedule.
Also schedules intelligence feed polling and update checks.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)


class AuditScheduler:
    """Scheduler for periodic SEO audits"""

    def __init__(self, timezone: str = "UTC", max_workers: int = 4):
        self.scheduler = AsyncIOScheduler(timezone=timezone, max_workers=max_workers)
        self._jobs: Dict[str, str] = {}  # project_id -> job_id

    async def start(self):
        """Start the scheduler"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    async def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    def schedule_project(
        self,
        project_id: str,
        cron_expression: str,
        callback: Callable,
        **callback_kwargs,
    ) -> str:
        """
        Schedule an audit for a project

        Args:
            project_id: e.g. "tentacl-ai"
            cron_expression: e.g. "0 7 * * 1" (Monday 7 AM)
            callback: Async function to invoke
            **callback_kwargs: Args for the callback function

        Returns:
            job_id
        """

        # Remove old job if present
        if project_id in self._jobs:
            old_job_id = self._jobs[project_id]
            try:
                self.scheduler.remove_job(old_job_id)
                logger.debug(f"Old job removed: {old_job_id}")
            except Exception:
                logger.debug(f"Job {old_job_id} already removed or not found")

        # Create new job
        try:
            trigger = CronTrigger.from_crontab(cron_expression)
            job = self.scheduler.add_job(
                callback,
                trigger=trigger,
                id=f"audit_{project_id}",
                name=f"Audit for {project_id}",
                kwargs=callback_kwargs,
                misfire_grace_time=300,  # 5 min grace period
            )

            self._jobs[project_id] = job.id
            logger.info(f"Job scheduled: {project_id} with cron '{cron_expression}'")
            return job.id

        except Exception as e:
            logger.error(f"Error scheduling {project_id}: {e}")
            raise

    def unschedule_project(self, project_id: str) -> bool:
        """Remove schedule for a project"""
        if project_id not in self._jobs:
            return False

        try:
            job_id = self._jobs[project_id]
            self.scheduler.remove_job(job_id)
            del self._jobs[project_id]
            logger.info(f"Job unscheduled: {project_id}")
            return True
        except Exception as e:
            logger.error(f"Error unscheduling {project_id}: {e}")
            return False

    def get_next_run(self, project_id: str) -> Optional[datetime]:
        """Get the next scheduled run time"""
        if project_id not in self._jobs:
            return None

        job_id = self._jobs[project_id]
        job = self.scheduler.get_job(job_id)
        return job.next_run_time if job else None

    def get_jobs(self) -> Dict[str, Dict]:
        """Liste alle geplanten Jobs"""
        jobs = {}
        for job in self.scheduler.get_jobs():
            jobs[job.id] = {
                "id": job.id,
                "name": job.name,
                "next_run_time": (
                    job.next_run_time.isoformat() if job.next_run_time else None
                ),
            }
        return jobs

    def schedule_intelligence_jobs(
        self,
        poll_callback: Callable,
        check_callback: Callable,
    ) -> None:
        """Schedule intelligence feed polling and update checks.

        - poll_feeds: every 6 hours (0, 6, 12, 18 UTC)
        - check_for_updates: daily at 8 UTC
        """
        self.scheduler.add_job(
            poll_callback,
            trigger=CronTrigger(hour="0,6,12,18"),
            id="intelligence_poll_feeds",
            name="Poll intelligence feeds",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Scheduled: intelligence_poll_feeds (every 6h)")

        self.scheduler.add_job(
            check_callback,
            trigger=CronTrigger(hour=8),
            id="intelligence_check_for_updates",
            name="Check for algorithm updates",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Scheduled: intelligence_check_for_updates (daily 08:00)")


# Singleton instance
scheduler = AuditScheduler()
