"""Scrapes sold items, order payouts, and transactions from Vinted."""

import re
from datetime import datetime, date
from typing import List, Optional
from rich.console import Console
from playwright.async_api import BrowserContext, Page
from src.config import settings
from src.models import SalesOrder
from src.scraper.browser import get_browser_context, human_delay
from src.scraper.closet import parse_price
from src.notifications import notify_auth_failure

console = Console()


async def scrape_vinted_orders() -> List[SalesOrder]:
    """
    Navigates to the seller's completed orders or transactions page to
    scrape sold articles, gross sale prices, platform fees, and net payouts.
    """
    console.print("[cyan]📦 Starting Vinted orders and sales scraper...[/cyan]")
    context: BrowserContext = await get_browser_context(headless=settings.headless, use_storage_state=True)
    page: Page = await context.new_page()

    orders: List[SalesOrder] = []

    try:
        # Navigate to orders or transaction history (Vinted uses /my_orders with underscore)
        target_url = f"{settings.vinted_base_url}/my_orders"
        console.print(f"[dim]Navigating to {target_url}...[/dim]")

        response = await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await human_delay(2.0, 3.0)

        # If redirected to login, alert session expiration
        if "login" in page.url or "auth" in page.url:
            console.print("[bold red]❌ Authentication required. Please run 'python -m src.cli login' to refresh session.[/bold red]")
            notify_auth_failure("Redirected to login while accessing orders")
            return []

        # Find order cards using Vinted's actual data-testid and inbox links
        order_elements = await page.query_selector_all(
            "[data-testid*='my-orders-item'], [data-testid*='order-item'], a[href*='/inbox/'], a[href*='/transaction/']"
        )

        console.print(f"[green]Found {len(order_elements)} order records.[/green]")

        for idx, elem in enumerate(order_elements):
            try:
                card_text = await elem.inner_text()
                lines = [l.strip() for l in card_text.split("\n") if l.strip()]

                # Filter out price and status lines to find the actual garment title
                title_candidates = [
                    l for l in lines
                    if not re.search(r"^\$\s*\d", l)
                    and "shipping label" not in l.lower()
                    and "order shipped" not in l.lower()
                    and "delivered" not in l.lower()
                    and "canceled" not in l.lower()
                    and "cancelled" not in l.lower()
                    and "completed" not in l.lower()
                ]
                title = title_candidates[0] if title_candidates else (lines[0] if lines else f"Sold Item {idx + 1}")

                # Extract price ($X.XX)
                price_matches = re.findall(r"\$\s*(\d+(?:\.\d{2})?)", card_text)
                sold_price = parse_price(price_matches[0]) if price_matches else 0.0

                # Extract Order ID from inbox/transaction link
                href = (await elem.get_attribute("href")) or ""
                if not href:
                    inbox_link = await elem.query_selector("a[href*='/inbox/'], a[href*='/transaction/']")
                    if inbox_link:
                        href = (await inbox_link.get_attribute("href")) or ""
                if "/" in href:
                    order_id = href.rstrip("/").split("/")[-1]
                else:
                    order_id = f"ORD-{int(datetime.now().timestamp())}-{idx}"

                # Status
                card_lower = card_text.lower()
                status = "Completed"
                if "shipping label" in card_lower or "in progress" in card_lower or "shipped" in card_lower:
                    status = "In Progress"
                elif "canceled" in card_lower or "cancelled" in card_lower:
                    status = "Canceled"

                # Seller fee (on Vinted, $0 seller fee)
                net_payout = sold_price

                order = SalesOrder(
                    order_id=order_id,
                    item_sku="",
                    item_title=title,
                    sale_date=date.today(),
                    sold_price=sold_price,
                    seller_fees=0.0,
                    net_payout=net_payout,
                    item_cost=0.0,
                    packaging_cost=0.0,
                    vinted_status=status,
                )
                orders.append(order)

            except Exception as e:
                console.print(f"[dim]Skipping order row due to parse error: {e}[/dim]")
                continue

    except Exception as e:
        console.print(f"[bold red]❌ Error scraping orders: {e}[/bold red]")
        raise
    finally:
        await context.close()

    console.print(f"[bold green]✨ Successfully scraped {len(orders)} sales orders.[/bold green]")
    return orders
