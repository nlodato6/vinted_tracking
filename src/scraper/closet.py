"""Scrapes active closet listings and current asking prices from Vinted."""

import re
from datetime import datetime, date
from typing import List, Optional
from rich.console import Console
from playwright.async_api import BrowserContext, Page
from src.config import settings
from src.models import ItemListing
from src.scraper.browser import get_browser_context, human_delay
from src.notifications import notify_auth_failure

console = Console()


def parse_price(price_str: str) -> float:
    """Extracts numeric price float from strings like '$45.00', '45,00 €', '$12'."""
    if not price_str:
        return 0.0
    cleaned = re.sub(r"[^\d.,]", "", price_str).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_vinted_id(url_or_str: str) -> str:
    """Extracts item ID from URL like '/items/489104821-vintage-coat'."""
    match = re.search(r"/items/(\d+)", url_or_str)
    if match:
        return match.group(1)
    digits = re.findall(r"\d+", url_or_str)
    return digits[0] if digits else ""


def infer_clothing_category(title: str) -> str:
    """Categorizes garment title into standardized apparel categories."""
    t = title.lower()
    if any(k in t for k in ["jacket", "coat", "parka", "windbreaker", "bomber", "puffer", "vest", "fleece"]):
        return "Outerwear"
    if any(k in t for k in ["hoodie", "sweatshirt", "sweater", "crewneck", "cardigan", "pullover", "knit"]):
        return "Sweaters & Hoodies"
    if any(k in t for k in ["t-shirt", "tee", "shirt", "polo", "jersey", "tank", "button up", "top", "blouse"]):
        return "Tops & Shirts"
    if any(k in t for k in ["jean", "denim", "pant", "trouser", "cargo", "chino", "jogger", "sweatpant", "short"]):
        return "Pants & Jeans"
    if any(k in t for k in ["shoe", "boot", "sneaker", "loafer", "slide", "sandal", "footwear"]):
        return "Footwear"
    if any(k in t for k in ["hat", "cap", "beanie", "belt", "bag", "backpack", "scarf", "wallet"]):
        return "Accessories"
    if any(k in t for k in ["dress", "skirt"]):
        return "Dresses & Skirts"
    return "Other Apparel"


def infer_brand(title: str, card_text: str) -> str:
    """Detects brand from card text or garment title."""
    popular_brands = [
        "Carhartt", "Nike", "Levi's", "Levis", "Adidas", "Ralph Lauren", "Polo",
        "The North Face", "North Face", "Patagonia", "Stussy", "Champion",
        "Harley Davidson", "Dickies", "Tommy Hilfiger", "Wrangler", "L.L. Bean",
        "Arc'teryx", "Arcteryx", "Supreme", "Zara", "H&M", "Columbia", "Vintage",
    ]
    combined = f"{title} {card_text}".lower()
    for brand in popular_brands:
        if brand.lower() in combined:
            return "Levi's" if brand.lower() == "levis" else brand
    return ""


async def extract_favorites(card) -> int:
    """Extracts favorites count from heart icon button, badge, or text."""
    try:
        # Check aria-label on button: e.g. "15 favourites" or "15 favorites"
        fav_elem = (
            await card.query_selector("[data-testid*='favourite'], [aria-label*='favourite'], [aria-label*='favorite']")
            or await card.query_selector("button[aria-label*='like'], span[class*='favourite-count']")
        )
        if fav_elem:
            aria_text = (await fav_elem.get_attribute("aria-label")) or ""
            count_match = re.search(r"(\d+)", aria_text)
            if count_match:
                return int(count_match.group(1))

            inner = await fav_elem.inner_text()
            inner_match = re.search(r"(\d+)", inner)
            if inner_match:
                return int(inner_match.group(1))

        # Check raw card text
        card_text = await card.inner_text()
        text_match = re.search(r"(\d+)\s*(?:favou?rites?|likes?)", card_text, re.IGNORECASE)
        if text_match:
            return int(text_match.group(1))
    except Exception:
        pass
    return 0


async def scrape_closet_listings(username: Optional[str] = None) -> List[ItemListing]:
    """
    Scrapes all listings from the seller's closet / items dashboard.
    Extracts title, asking price, item ID, favorites count, category, and status.
    """
    console.print("[cyan]🔍 Starting Vinted closet scraper...[/cyan]")
    context: BrowserContext = await get_browser_context(headless=settings.headless, use_storage_state=True)
    page: Page = await context.new_page()

    items: List[ItemListing] = []

    try:
        # Navigate to member items dashboard or public closet
        target_username = username or settings.vinted_username
        if target_username:
            target_url = f"{settings.vinted_base_url}/member/{target_username}"
        else:
            target_url = f"{settings.vinted_base_url}/member/items"

        console.print(f"[dim]Navigating to {target_url}...[/dim]")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await human_delay(2.0, 3.5)

        if "login" in page.url or "auth" in page.url:
            console.print("[bold red]❌ Authentication required. Please run 'python -m src.cli login' to refresh session.[/bold red]")
            notify_auth_failure("Redirected to login while accessing closet listings")
            return []

        # Scroll to load dynamically rendered items (infinite scroll)
        for _ in range(3):
            await page.mouse.wheel(0, 1500)
            await human_delay(1.0, 1.5)

        # Find item card elements with resilient selectors
        item_cards = await page.query_selector_all(
            "[data-testid*='grid-item'], div[class*='feed-grid__item'], div[class*='ItemBox_item-box']"
        )

        if not item_cards:
            item_cards = await page.query_selector_all("a[href*='/items/']")

        console.print(f"[green]Found {len(item_cards)} listing cards on page.[/green]")

        seen_ids = set()

        for card in item_cards:
            try:
                link_elem = await card.query_selector("a[href*='/items/']") or card
                href = await link_elem.get_attribute("href") or ""
                if not href:
                    continue

                full_url = href if href.startswith("http") else f"{settings.vinted_base_url}{href}"
                v_id = extract_vinted_id(full_url)
                if not v_id or v_id in seen_ids:
                    continue
                seen_ids.add(v_id)

                # Extract title and brand from Vinted's rich overlay title or img alt:
                # e.g. "Face Print Pants, brand: Shein, condition: Good, size: S / US 4-6, $2.00"
                overlay_title = (await link_elem.get_attribute("title")) or ""
                img_elem = await card.query_selector("img[alt]")
                img_alt = (await img_elem.get_attribute("alt")) if img_elem else ""
                raw_meta = overlay_title or img_alt

                title = ""
                extracted_brand = ""
                if raw_meta and ", brand:" in raw_meta:
                    parts = raw_meta.split(", brand:")
                    title = parts[0].strip()
                    extracted_brand = parts[1].split(",")[0].strip()
                elif raw_meta and "," in raw_meta:
                    title = raw_meta.split(",")[0].strip()
                elif raw_meta:
                    title = raw_meta.strip()

                if not title:
                    title_elem = (
                        await card.query_selector("[data-testid*='title']")
                        or await card.query_selector("h3, h4, p[class*='title']")
                    )
                    if title_elem:
                        title = (await title_elem.inner_text()).strip()

                if not title or "views" in title.lower():
                    slug_match = re.search(r"/items/\d+-([\w-]+)", full_url)
                    title = slug_match.group(1).replace("-", " ").title() if slug_match else f"Vinted Item {v_id}"

                # Extract price (both original struck-through price if discounted, and current asking price)
                original_price = 0.0
                orig_elem = await card.query_selector("del, s, [class*='strike'], [class*='old'], [data-testid*='original-price']")
                if orig_elem:
                    orig_text = await orig_elem.inner_text()
                    original_price = parse_price(orig_text)

                price_elem = (
                    await card.query_selector("[data-testid*='price']")
                    or await card.query_selector("p[class*='price'], span[class*='price']")
                )
                price_text = await price_elem.inner_text() if price_elem else ""
                current_price = parse_price(price_text)

                card_raw_text = await card.inner_text()
                if not current_price:
                    price_match = re.search(r"\$\s*(\d+(?:\.\d{2})?)", card_raw_text)
                    if price_match:
                        current_price = parse_price(price_match.group(0))

                if not original_price:
                    original_price = current_price

                # Extract favorites
                favorites_count = await extract_favorites(card)

                # Extract views if present
                views_match = re.search(r"(\d+)\s*views?", card_raw_text, re.IGNORECASE)
                views_count = int(views_match.group(1)) if views_match else 0

                # Status detection
                card_text_lower = card_raw_text.lower()
                status = "Listed"
                if "sold" in card_text_lower:
                    status = "Sold"
                elif "reserved" in card_text_lower:
                    status = "Reserved"

                # Infer brand and category
                brand = extracted_brand or infer_brand(title, card_raw_text)
                category = infer_clothing_category(title)

                item = ItemListing(
                    item_sku="",
                    title=title.strip(),
                    brand=brand,
                    category=category,
                    posted_price=original_price,
                    target_price=original_price,
                    current_price=current_price,
                    favorites=favorites_count,
                    views=views_count,
                    vinted_id=v_id,
                    vinted_url=full_url,
                    status=status,
                    date_listed=date.today(),
                    last_synced=datetime.now(),
                )
                items.append(item)

            except Exception as e:
                console.print(f"[dim]Warning: Skipping one item card due to error: {e}[/dim]")
                continue

    except Exception as e:
        console.print(f"[bold red]❌ Error while scraping closet: {e}[/bold red]")
        raise
    finally:
        await context.close()

    console.print(f"[bold green]✨ Successfully scraped {len(items)} closet items.[/bold green]")
    return items
