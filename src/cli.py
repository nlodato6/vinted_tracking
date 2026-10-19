"""Command-line interface for the Vinted sales tracking and sync pipeline."""

import asyncio
import typer
from rich.console import Console
from rich.table import Table

from src.config import settings
from src.scraper import interactive_login, verify_session, parse_and_save_cookies, scrape_closet_listings, scrape_vinted_orders
from src.sheets import GoogleSheetsClient, initialize_all_tabs, SheetsSyncEngine
from src.notifications import notify_auth_failure, notify_auth_success

app = typer.Typer(help="Vinted Automated Sales & Inventory Tracking CLI")
console = Console()


@app.command()
def login():
    """Launch interactive browser to log in to Vinted and save authentication session."""
    asyncio.run(interactive_login())


@app.command()
def import_cookie(
    file: typer.Option(None, "--file", "-f", help="Path to exported cookie JSON file from browser"),
):
    """Import active Vinted session cookies directly from your regular browser."""
    console.print("\n[bold cyan]🍪 Vinted Session Cookie Importer[/bold cyan]")
    
    if file:
        file_path = typer.Path(file)
        import Path as _P
        from pathlib import Path
        p = Path(file)
        if not p.exists():
            console.print(f"[bold red]❌ File not found: {file}[/bold red]")
            raise typer.Exit(code=1)
        raw_text = p.read_text(encoding="utf-8")
    else:
        console.print("[dim]Tip: You can export cookies from your regular browser (Chrome/Safari) using:[/dim]")
        console.print("[dim]  Option A: A free extension like 'Cookie-Editor' (Click Export -> Export as JSON).[/dim]")
        console.print("[dim]  Option B: Chrome DevTools Console on vinted.com -> type: copy(document.cookie)[/dim]")
        console.print("\n[yellow]Paste your cookie JSON or cookie header string below, then press [ENTER]:[/yellow]")
        raw_text = input().strip()

    if not raw_text:
        console.print("[bold red]❌ No cookie data provided.[/bold red]")
        raise typer.Exit(code=1)

    try:
        saved_path = parse_and_save_cookies(raw_text)
        console.print(f"[bold green]✅ Session successfully imported and saved to {saved_path}![/bold green]")
        notify_auth_success()
        console.print("[dim]You can verify it anytime by running: python -m src.cli check-session[/dim]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Failed to import cookies: {e}[/bold red]")
        raise typer.Exit(code=1)


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
        notify_auth_success()
    except Exception as e:
        console.print(f"[bold red]❌ Error uploading to GitHub: {e}[/bold red]")
        raise typer.Exit(code=1)


@app.command()
def clear_sample_data():
    """Remove the initial placeholder demo rows so your spreadsheet starts 100% clean."""
    console.print("\n[bold cyan]🧹 Clearing demo placeholder rows from Google Sheets...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        sh = client.get_spreadsheet()
        for tab_name in ["Inventory_Lots", "Item_Listings", "Sold_Items"]:
            try:
                ws = sh.worksheet(tab_name)
                rows = ws.get_all_values()
                if len(rows) > 1:
                    ws.delete_rows(2, len(rows))
                    console.print(f"[dim]Cleaned demo rows from {tab_name}[/dim]")
            except Exception:
                pass
        console.print("[bold green]✅ All demo rows removed! Spreadsheet is now fresh and ready for your real inventory.[/bold green]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Error clearing sample rows: {e}[/bold red]")
        raise typer.Exit(code=1)


@app.command()
def init_sheet():
    """Provision Google Spreadsheet with all tabs, formulas, and KPI dashboard."""
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
    Log an external sale (e.g. Facebook Marketplace), move the item to Sold_Items,
    and record all profit metrics without redundant duplicate sheets.
    """
    from datetime import date, datetime
    console.print(f"\n[bold cyan]📝 Logging sale on {platform}...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)
        sh = client.get_spreadsheet()
        ws_sold = sh.worksheet("Sold_Items")

        sku_clean = sku.strip().upper()
        sale_dt = date_sold or date.today().strftime("%Y-%m-%d")
        order_ref = f"ORD-{datetime.now().strftime('%Y%m%d%H%M')}"

        # Move item from active Item_Listings to Sold_Items with full sale details
        moved = engine.move_listing_to_sold(
            sku=sku_clean,
            status=f"Sold ({platform})",
            sold_price=price,
            sale_date=sale_dt,
            platform=platform,
            order_id=order_ref,
        )
        item_title = sku_clean
        if moved:
            item_title = moved.get("Title") or sku_clean
            console.print(f"[bold green]✅ Moved '{item_title}' ({sku_clean}) to Sold_Items at ${price:.2f}![/bold green]")
        else:
            # Check if it was already moved or exists in Sold_Items
            sold_rows = ws_sold.get_all_values()
            found_in_sold = False
            if len(sold_rows) > 1:
                s_headers = sold_rows[0]
                s_sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
                s_title_idx = s_headers.index("Title") if "Title" in s_headers else 2
                s_sold_price_idx = s_headers.index("Sold_Price") if "Sold_Price" in s_headers else 11
                s_plat_idx = s_headers.index("Platform") if "Platform" in s_headers else 15
                s_date_idx = s_headers.index("Sale_Date") if "Sale_Date" in s_headers else 14
                for r_i, sr in enumerate(sold_rows[1:], start=2):
                    if len(sr) > s_sku_idx and sr[s_sku_idx].strip().upper() == sku_clean:
                        item_title = sr[s_title_idx] if len(sr) > s_title_idx else sku_clean
                        found_in_sold = True
                        col_sp = chr(ord('A') + s_sold_price_idx)
                        col_plat = chr(ord('A') + s_plat_idx)
                        col_dt = chr(ord('A') + s_date_idx)
                        ws_sold.update_acell(f"{col_sp}{r_i}", price)
                        ws_sold.update_acell(f"{col_plat}{r_i}", platform)
                        ws_sold.update_acell(f"{col_dt}{r_i}", sale_dt)
                        console.print(f"[bold green]✅ Updated existing Sold_Items record for '{item_title}' ({sku_clean}) to ${price:.2f}![/bold green]")
                        break
            if not found_in_sold:
                console.print(f"[bold red]❌ SKU '{sku}' not found in Item_Listings or Sold_Items.[/bold red]")
                raise typer.Exit(code=1)

        engine.sync_lots()
        console.print(f"[dim]Platform: {platform} | Sold Price: ${price:.2f} | Sale Date: {sale_dt}[/dim]\n")
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

    if sync_type in ("listings", "sales", "all") and not settings.full_storage_state_path.exists():
        console.print("[bold red]❌ No saved session file found at .session/vinted_state.json.[/bold red]")
        console.print("[yellow]Please run 'python -m src.cli login' to authenticate.[/yellow]")
        notify_auth_failure("Missing session storage state file (.session/vinted_state.json)")
        raise typer.Exit(code=1)

    async def _execute():
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)

        if sync_type in ("listings", "all"):
            console.print("[yellow]1/2 Scraped closet items...[/yellow]")
            items = await scrape_closet_listings()
            res = engine.sync_listings(items)
            console.print(f"[green]✔ Listings synced: {res['updated']} updated, {res['created']} newly added.[/green]")

        if sync_type in ("sales", "all"):
            console.print("[yellow]2/3 Scraped sold orders...[/yellow]")
            orders = await scrape_vinted_orders()
            res = engine.sync_orders(orders)
            console.print(f"[green]✔ Orders synced: {res['synced_orders']} new sales logged.[/green]")

        if sync_type in ("lots", "all"):
            console.print("[yellow]3/3 Updating inventory lots & statuses...[/yellow]")
            lot_res = engine.sync_lots()
            console.print(f"[green]✔ Inventory Lots synced: {lot_res['total_lots']} lots tracked ({lot_res['completed_lots']} completed, {lot_res['active_lots']} active).[/green]")

        notify_auth_success()
        console.print("\n[bold green]🎉 Sync finished successfully![/bold green]\n")

    try:
        asyncio.run(_execute())
    except Exception as e:
        console.print(f"[bold red]❌ Sync error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def sync_lots():
    """Recalculate unit costs, items processed, and lot completion status in Inventory_Lots."""
    console.print("\n[bold cyan]📦 Synchronizing Inventory Lots...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)
        res = engine.sync_lots()
        console.print(f"[bold green]✅ Synced {res['total_lots']} lots ({res['completed_lots']} completed, {res['active_lots']} active).[/bold green]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Error syncing lots: {e}[/bold red]")
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
