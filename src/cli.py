import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.config import settings
from src.html_parser import parse_closet_html, parse_orders_html
from src.sheets import GoogleSheetsClient, initialize_all_tabs, SheetsSyncEngine

app = typer.Typer(help="Vinted Sales & Inventory Tracking CLI (HTML & Email Automation)")
console = Console()


@app.command()
def import_closet(
    file_path: str = typer.Argument(..., help="Path to saved Vinted closet/profile HTML file"),
    lot: Optional[str] = typer.Option("LOT-001", "--lot", "-l", help="Lot ID to assign to new active items"),
):
    """
    Import active listings from a saved Vinted closet HTML file directly into Google Sheets.
    """
    p = Path(file_path)
    if not p.exists():
        console.print(f"[bold red]❌ HTML file not found: {file_path}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]📦 Parsing closet listings from '{p.name}'...[/bold cyan]")
    items = parse_closet_html(p, default_lot=lot or "")
    if not items:
        console.print("[yellow]⚠ No item cards found in this HTML file.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[green]Found {len(items)} items in HTML file. Syncing to Google Sheets...[/green]")
    client = GoogleSheetsClient()
    engine = SheetsSyncEngine(client)
    res = engine.sync_listings(items)
    console.print(f"[bold green]✔ Done! {res['updated']} existing items updated, {res['created']} newly added.[/bold green]\n")
    engine.sync_lots()


@app.command()
def import_orders(
    file_path: str = typer.Argument(..., help="Path to saved Vinted 'My orders' / sales HTML file"),
):
    """
    Import sold orders from a saved Vinted 'My orders' HTML file directly into Google Sheets.
    """
    p = Path(file_path)
    if not p.exists():
        console.print(f"[bold red]❌ HTML file not found: {file_path}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]💰 Parsing sold orders from '{p.name}'...[/bold cyan]")
    orders = parse_orders_html(p)
    if not orders:
        console.print("[yellow]⚠ No order records found in this HTML file.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[green]Found {len(orders)} orders in HTML file. Syncing to Google Sheets...[/green]")
    client = GoogleSheetsClient()
    engine = SheetsSyncEngine(client)
    res = engine.sync_orders(orders)
    console.print(f"[bold green]✔ Done! {res['synced_orders']} orders synced into Sold_Items.[/bold green]\n")
    engine.sync_lots()


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

    # Pipeline Mode
    table.add_row("Pipeline Mode", "HTML & Email Automation", "[green]Active (No Bot Scraper)[/green]")

    console.print(table)


if __name__ == "__main__":
    app()
