"""Background scheduler for periodic synchronization of Vinted listings and sales."""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from rich.console import Console
from src.config import settings
from src.server import run_sync_task

console = Console()


async def scheduled_sync_job():
    """Periodic job executed by the scheduler."""
    console.print("\n[bold blue]⏰ Periodic sync job triggered by scheduler...[/bold blue]")
    await run_sync_task("all")


def start_scheduler():
    """Initializes and runs the AsyncIOScheduler."""
    scheduler = AsyncIOScheduler()
    interval_hours = settings.sync_interval_hours

    scheduler.add_job(
        scheduled_sync_job,
        "interval",
        hours=interval_hours,
        id="vinted_periodic_sync",
        replace_existing=True,
    )

    console.print(f"[bold green]🕒 Scheduler active! Running full sync every {interval_hours} hour(s).[/bold green]")
    scheduler.start()

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        console.print("[yellow]Stopping scheduler...[/yellow]")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
