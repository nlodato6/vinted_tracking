"""Background scheduler for periodic synchronization of Vinted listings and sales."""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from rich.console import Console
from src.config import settings
from src.server import run_sync_task

console = Console()


async def scheduled_sync_job():
    """Periodic job executed by the scheduler."""
    console.print("\n[bold blue]⏰ Daily sync job triggered by scheduler...[/bold blue]")
    await run_sync_task("all")


def build_scheduler() -> tuple[AsyncIOScheduler, str]:
    """Configures the AsyncIOScheduler instance based on settings."""
    scheduler = AsyncIOScheduler()
    if settings.sync_mode.lower() == "interval":
        trigger = IntervalTrigger(hours=settings.sync_interval_hours)
        desc = f"every {settings.sync_interval_hours} hour(s)"
    else:
        trigger = CronTrigger(
            hour=settings.sync_schedule_hour,
            minute=settings.sync_schedule_minute,
        )
        desc = f"daily at {settings.sync_schedule_hour:02d}:{settings.sync_schedule_minute:02d} local time"

    scheduler.add_job(
        scheduled_sync_job,
        trigger=trigger,
        id="vinted_sync_job",
        replace_existing=True,
    )
    return scheduler, desc


def start_scheduler():
    """Initializes and runs the AsyncIOScheduler."""
    scheduler, desc = build_scheduler()
    console.print(f"[bold green]🕒 Scheduler active! Running full sync {desc}.[/bold green]")
    scheduler.start()

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        console.print("[yellow]Stopping scheduler...[/yellow]")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
