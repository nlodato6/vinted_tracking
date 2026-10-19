"""Command-line interface for the Vinted sales tracking and sync pipeline."""

import asyncio
import typer
from rich.console import Console
from rich.table import Table

from src.config import settings
from src.scraper import interactive_login, verify_session, scrape_closet_listings, scrape_vinted_orders
from src.sheets import GoogleSheetsClient, initialize_all_tabs, SheetsSyncEngine

app = typer.Typer(help="Vinted Automated Sales & Inventory Tracking CLI")
console = Console()


@app.command()
def login():
    """Launch interactive browser to log in to Vinted and save authentication session."""
    asyncio.run(interactive_login())


@app.command()
def check_session():
    """Verify if the current saved Vinted session is active and valid."""
    is_valid = asyncio.run(verify_session())
    if not is_valid:
        raise typer.Exit(code=1)


@app.command()
def init_sheet():
    """Provision Google Spreadsheet with all 5 tabs, formulas, and KPI dashboard."""
    console.print("\n[bold cyan]📊 Connecting to Google Sheets...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        sh = initialize_all_tabs(client)
        console.print(f"[bold green]✅ Successfully initialized Google Sheet:[/bold green] '{sh.title}'")
        console.print(f"[dim]URL: https://docs.google.com/spreadsheets/d/{sh.id}/edit[/dim]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Failed to initialize Google Sheet:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def sync(
    sync_type: str = typer.Option("all", "--type", "-t", help="Sync type: 'listings', 'sales', or 'all'"),
):
    """
    On-demand synchronization between Vinted and Google Sheets.
    Use this after listing new items or making a sale.
    """
    console.print(f"\n[bold cyan]🔄 Initiating manual sync: type='{sync_type}'...[/bold cyan]")

    async def _execute():
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)

        if sync_type in ("listings", "all"):
            console.print("[yellow]1/2 Scraped closet items...[/yellow]")
            items = await scrape_closet_listings()
            res = engine.sync_listings(items)
            console.print(f"[green]✔ Listings synced: {res['updated']} updated, {res['created']} newly added.[/green]")

        if sync_type in ("sales", "all"):
            console.print("[yellow]2/2 Scraped sold orders...[/yellow]")
            orders = await scrape_vinted_orders()
            res = engine.sync_orders(orders)
            console.print(f"[green]✔ Orders synced: {res['synced_orders']} new sales logged.[/green]")

        console.print("\n[bold green]🎉 Sync finished successfully![/bold green]\n")

    try:
        asyncio.run(_execute())
    except Exception as e:
        console.print(f"[bold red]❌ Sync error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option(settings.webhook_host, "--host", "-h", help="Bind host"),
    port: int = typer.Option(settings.webhook_port, "--port", "-p", help="Bind port"),
):
    """Start FastAPI server for inbound email webhooks and remote triggers."""
    import uvicorn
    console.print(f"[bold green]🚀 Launching Webhook Server on {host}:{port}...[/bold green]")
    uvicorn.run("src.server:app", host=host, port=port, reload=False)


@app.command()
def scheduler():
    """Start the periodic background synchronization service."""
    from src.scheduler import start_scheduler
    start_scheduler()


@app.command()
def status():
    """Display system status, configuration, and credentials."""
    table = Table(title="Vinted Tracking Pipeline Status", show_header=True)
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Configuration", style="magenta")
    table.add_column("Status", style="green")

    # Service Account
    sa_path = settings.google_service_account_file
    sa_exists = sa_path.exists()
    client = GoogleSheetsClient()
    email = client.service_account_email or "Not found"
    table.add_row(
        "Google Service Account",
        str(sa_path.resolve()),
        f"[green]Found ({email})[/green]" if sa_exists else "[yellow]Missing credentials.json[/yellow]",
    )

    # Google Sheet
    sheet_id = settings.google_sheet_id or "Auto-match title"
    table.add_row("Google Sheet Target", sheet_id, "[green]Configured[/green]" if settings.google_sheet_id else "[yellow]Using title[/yellow]")

    # Vinted Session
    session_file = settings.full_storage_state_path
    session_exists = session_file.exists()
    table.add_row(
        "Vinted Session",
        str(session_file),
        "[green]Active state saved[/green]" if session_exists else "[yellow]No session (run login)[/yellow]",
    )

    # Marketplace Domain
    table.add_row("Vinted Domain", settings.vinted_domain, "[green]OK[/green]")

    console.print(table)


if __name__ == "__main__":
    app()
