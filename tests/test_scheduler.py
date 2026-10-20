from src.scheduler import build_scheduler
from src.config import settings
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


def test_build_scheduler_cron():
    settings.sync_mode = "cron"
    settings.sync_schedule_hour = 8
    settings.sync_schedule_minute = 0

    scheduler, desc = build_scheduler()
    assert "daily at 08:00" in desc
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "vinted_sync_job"
    assert isinstance(jobs[0].trigger, CronTrigger)


def test_build_scheduler_interval():
    settings.sync_mode = "interval"
    settings.sync_interval_hours = 12

    scheduler, desc = build_scheduler()
    assert "every 12 hour(s)" in desc
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "vinted_sync_job"
    assert isinstance(jobs[0].trigger, IntervalTrigger)

    # Reset
    settings.sync_mode = "cron"
    settings.sync_schedule_hour = 8
    settings.sync_schedule_minute = 0
