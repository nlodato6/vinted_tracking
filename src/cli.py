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
def upload_session():
    """Upload local Vinted session tokens to GitHub Secrets for cloud automation."""
    import subprocess
    session_path = settings.full_storage_state_path
    if not session_path.exists():
        console.print("[bold red]❌ No local session file found at .session/vinted_state.json.[/bold red]")
        console.print("[yellow]Please run 'python -m src.cli login' first to authenticate.[/yellow]")
        raise typer.Exit(code=1)

    console.print("\n[bold cyan]☁️ Uploading Vinted session to GitHub Secrets...[/bold cyan]")
    try:
        with open(session_path, "rb") as f:
            subprocess.run(
                ["gh", "secret", "set", "VINTED_STORAGE_STATE", "--repo", "nlodato6/vinted_tracking"],
                stdin=f,
                check=True,
            )
        console.print("[bold green]✅ Successfully uploaded VINTED_STORAGE_STATE to GitHub![/bold green]")
        console.print("[dim]GitHub Actions can now run scheduled syncs in the cloud with your laptop closed.[/dim]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Error uploading to GitHub: {e}[/bold red]")
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
def log_sale(
    sku: str = typer.Option(..., "--sku", "-s", help="Item SKU (e.g. SKU-1001)"),
    price: float = typer.Option(..., "--price", "-p", help="Gross sold price"),
    platform: str = typer.Option("FB Marketplace (Local)", "--platform", help="Platform: 'FB Marketplace (Local)', 'FB Marketplace (Shipped)', 'Depop', 'Other'"),
    fees: float = typer.Option(0.0, "--fees", help="Platform fees (0 for cash/local)"),
    date_sold: str = typer.Option("", "--date", help="Sale date YYYY-MM-DD (defaults to today)"),
):
    """
    Log an external sale (e.g. Facebook Marketplace) and mark the item as sold.
    Automatically links unit cost and calculates net profit.
    """
    from datetime import date, datetime
    console.print(f"\n[bold cyan]📝 Logging sale on {platform}...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        sh = client.get_spreadsheet()
        ws_listings = sh.worksheet("Item_Listings")
        ws_sales = sh.worksheet("Sales_Log")

        all_listings = ws_listings.get_all_values()
        sku_clean = sku.strip().upper()
        target_row = None
        item_title = ""

        if len(all_listings) > 1:
            for r_idx, r in enumerate(all_listings[1:], start=2):
                if r and r[0].strip().upper() == sku_clean:
                    target_row = r_idx
                    item_title = r[2] if len(r) > 2 else sku_clean
                    break

        if not target_row:
            console.print(f"[bold red]❌ SKU '{sku}' not found in Item_Listings.[/bold red]")
            raise typer.Exit(code=1)

        # Update Item_Listings status to Sold (Platform)
        ws_listings.update(f"M{target_row}", [[f"Sold ({platform})"]])

        # Append to Sales_Log
        sales_rows = ws_sales.get_all_values()
        next_row_num = len(sales_rows) + 1
        sale_dt = date_sold or date.today().strftime("%Y-%m-%d")
        order_ref = f"FB-{datetime.now().strftime('%Y%m%d%H%M')}"
        net_payout = round(price - fees, 2)

        new_row = [
            order_ref,
            platform,
            sku_clean,
            item_title,
            f'=IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:E, 4, FALSE), "")',
            f'=IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:E, 5, FALSE), "")',
            f'=IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:I, 9, FALSE), 0)',
            sale_dt,
            price,
            fees,
            net_payout,
            f"=IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:F, 6, FALSE), 0)",
            f'=IF(B{next_row_num}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
            f"=K{next_row_num}-L{next_row_num}-M{next_row_num}",
            f"=IF(I{next_row_num}>0, N{next_row_num}/I{next_row_num}, 0)",
            "Completed",
        ]
        ws_sales.append_row(new_row, value_input_option="USER_ENTERED")
        console.print(f"[bold green]✅ Logged '{item_title}' ({sku_clean}) as sold on {platform} for ${price:.2f}![/bold green]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Error logging sale: {e}[/bold red]")
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
