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
        # Navigate to orders or transaction history
        target_url = f"{settings.vinted_base_url}/my-orders"
        console.print(f"[dim]Navigating to {target_url}...[/dim]")

        response = await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await human_delay(2.0, 3.0)

        # If redirected to login, alert session expiration
        if "login" in page.url:
            console.print("[bold red]❌ Authentication required. Please run 'python -m src.cli login' to refresh session.[/bold red]")
            return []

        # Find order cards
        order_elements = await page.query_selector_all(
            "[data-testid*='order-item'], div[class*='OrderCard'], div[class*='transaction-item'], tr[class*='order']"
        )

        console.print(f"[green]Found {len(order_elements)} order records.[/green]")

        for idx, elem in enumerate(order_elements):
            try:
                card_text = await elem.inner_text()

                # Extract title
                title_elem = (
                    await elem.query_selector("[data-testid*='title']")
                    or await elem.query_selector("h3, h4, span[class*='title'], a[href*='/items/']")
                )
                title = (await title_elem.inner_text()).strip() if title_elem else f"Sold Item {idx + 1}"

                # Extract price
                price_matches = re.findall(r"\$\s*\d+(?:\.\d{2})?", card_text)
                sold_price = parse_price(price_matches[0]) if price_matches else 0.0

                # Extract Order ID
                order_id_match = re.search(r"(?:order|transaction|id)\s*#?\s*([A-Za-z0-9-]+)", card_text, re.IGNORECASE)
                order_id = order_id_match.group(1) if order_id_match else f"ORD-{int(datetime.now().timestamp())}-{idx}"

                # Status
                status = "Completed"
                if "in transit" in card_text.lower():
                    status = "In Transit"
                elif "delivered" in card_text.lower():
                    status = "Delivered"

                # Seller fee (on Vinted US, buyer typically pays protection fee; seller payout is usually equal to item price)
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
