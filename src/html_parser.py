"""HTML Parser for locally saved Vinted pages (closet and orders)."""

import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union
from bs4 import BeautifulSoup
from src.models import ItemListing, SalesOrder


def parse_price(price_val: Union[str, float, int, None]) -> float:
    """Extracts numeric price float from strings like '$45.00', '45,00 €', or numeric values."""
    if price_val is None:
        return 0.0
    if isinstance(price_val, (int, float)):
        return float(price_val)
    cleaned = re.sub(r"[^\d.,]", "", str(price_val)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_vinted_id(url_or_str: str) -> str:
    """Extracts numeric Vinted ID from URL or string."""
    m = re.search(r"/items/(\d+)", str(url_or_str))
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{7,12})\b", str(url_or_str))
    return m.group(1) if m else ""


def infer_clothing_category(title: str) -> str:
    """Infers high-level clothing category from garment title."""
    t = title.lower()
    if any(k in t for k in ["jacket", "coat", "blazer", "parka", "windbreaker", "vest"]):
        return "Outerwear"
    if any(k in t for k in ["sweater", "hoodie", "sweatshirt", "cardigan", "pullover", "fleece"]):
        return "Sweaters & Hoodies"
    if any(k in t for k in ["shirt", "tee", "top", "blouse", "polo", "tank"]):
        return "Tops & Shirts"
    if any(k in t for k in ["jean", "pant", "trouser", "denim", "short", "legging", "jogger", "sweatpant"]):
        return "Pants & Jeans"
    if any(k in t for k in ["shoe", "boot", "sneaker", "heel", "sandal", "loafer", "flat"]):
        return "Footwear"
    if any(k in t for k in ["bag", "purse", "backpack", "tote", "hat", "cap", "belt", "scarf", "sunglass", "wallet", "jewelry", "necklace"]):
        return "Accessories"
    if any(k in t for k in ["dress", "skirt"]):
        return "Dresses & Skirts"
    return "Other Apparel"


def infer_brand(title: str, card_text: str = "") -> str:
    """Detects brand from card text or garment title."""
    popular_brands = [
        "Carhartt", "Nike", "Levi's", "Levis", "Levi", "Adidas", "Ralph Lauren", "Polo",
        "The North Face", "North Face", "Patagonia", "Stussy", "Champion",
        "Harley Davidson", "Dickies", "Tommy Hilfiger", "Wrangler", "L.L. Bean",
        "Arc'teryx", "Arcteryx", "Supreme", "Zara", "H&M", "Columbia", "Coach",
        "Free People", "Abercrombie", "American Eagle", "BCBG", "Calvin Klein",
    ]
    combined = f"{title} {card_text}".lower()
    for brand in popular_brands:
        if brand.lower() in combined:
            return "Levi's" if brand.lower() in ("levis", "levi") else brand
    return ""


def parse_closet_html(html_content_or_path: Union[str, Path], default_lot: str = "") -> List[ItemListing]:
    """
    Parses a saved Vinted closet/profile HTML file or string.
    Extracts all listings with title, asking price, Vinted ID, favorites, and status.
    """
    if isinstance(html_content_or_path, Path) or (isinstance(html_content_or_path, str) and "\n" not in html_content_or_path and Path(html_content_or_path).exists()):
        with open(html_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    else:
        html = str(html_content_or_path)

    soup = BeautifulSoup(html, "html.parser")
    items: List[ItemListing] = []
    seen_ids = set()

    # Strategy 1: Look for item cards via feed-grid or item box
    cards = soup.select("[data-testid*='grid-item'], div[class*='feed-grid__item'], div[class*='ItemBox_item-box'], div[class*='web_ui__ItemBox__box']")
    if not cards:
        cards = soup.select("a[href*='/items/']")

    for card in cards:
        text = card.get_text(separator=" ", strip=True)
        link = card if card.name == "a" and "/items/" in (card.get("href") or "") else card.find("a", href=re.compile(r"/items/\d+"))
        if not link:
            continue

        href = link.get("href", "")
        v_id = extract_vinted_id(href)
        if not v_id or v_id in seen_ids:
            continue
        seen_ids.add(v_id)

        full_url = f"https://www.vinted.com/items/{v_id}"

        # Extract title
        title = link.get("title", "") or ""
        img = card.find("img")
        if not title and img and img.get("alt"):
            title = img.get("alt", "").strip()
        if not title:
            # Try to extract title from text or url slug
            slug_match = re.search(r"/items/\d+-([a-zA-Z0-9-]+)", href)
            if slug_match:
                title = slug_match.group(1).replace("-", " ").title()

        # Extract price
        price_match = re.search(r"\$\s*(\d+(?:\.\d{2})?)", text)
        price_val = float(price_match.group(1)) if price_match else 0.0

        # Extract favorites
        fav_match = re.search(r"(\d+)\s*(?:favou?rites?|likes?)", text, re.IGNORECASE)
        fav_count = int(fav_match.group(1)) if fav_match else 0

        # Check status
        is_sold = bool(re.search(r"\b(?:sold|closed)\b", text, re.IGNORECASE))
        status = "Sold" if is_sold else "Listed"

        brand = infer_brand(title, text)
        category = infer_clothing_category(title)

        listing = ItemListing(
            item_sku="",
            lot_id=default_lot,
            title=title or f"Vinted Item {v_id}",
            brand=brand,
            category=category,
            posted_price=price_val,
            target_price=price_val,
            current_price=price_val,
            sold_price=price_val if is_sold else None,
            favorites=fav_count,
            views=0,
            vinted_id=v_id,
            vinted_url=full_url,
            status=status,
            date_listed=date.today(),
            last_synced=datetime.now(),
        )
        items.append(listing)

    return items


def parse_orders_html(html_content_or_path: Union[str, Path]) -> List[SalesOrder]:
    """
    Parses a saved Vinted 'My orders' / sales history HTML page.
    Extracts Order IDs, item titles, sold prices, and sale dates.
    """
    if isinstance(html_content_or_path, Path) or (isinstance(html_content_or_path, str) and "\n" not in html_content_or_path and Path(html_content_or_path).exists()):
        with open(html_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    else:
        html = str(html_content_or_path)

    soup = BeautifulSoup(html, "html.parser")
    orders: List[SalesOrder] = []
    seen_order_ids = set()

    # Look for order cards or links
    order_elements = soup.select("a[href*='/transaction/'], a[href*='/inbox/'], div[class*='order'], div[class*='transaction']")
    if not order_elements:
        order_elements = soup.find_all(text=re.compile(r"Order|Transaction|Sold", re.IGNORECASE))

    for elem in order_elements:
        node = elem if hasattr(elem, "find_all") else elem.parent
        text = node.get_text(separator=" ", strip=True) if node else ""

        # Find transaction or inbox link
        link = node.find("a", href=re.compile(r"/(?:transaction|inbox)/([a-zA-Z0-9-]+)")) if node else None
        order_id = ""
        if link:
            m = re.search(r"/(?:transaction|inbox)/([a-zA-Z0-9-]+)", link.get("href", ""))
            if m:
                order_id = m.group(1)

        if not order_id:
            continue
        if order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)

        # Extract price
        price_match = re.search(r"\$\s*(\d+(?:\.\d{2})?)", text)
        sold_price = float(price_match.group(1)) if price_match else 0.0

        # Extract item link if present
        v_url = ""
        item_link = node.find("a", href=re.compile(r"/items/(\d+)"))
        if item_link:
            v_url = f"https://www.vinted.com{item_link.get('href')}" if item_link.get('href').startswith('/') else item_link.get('href')

        # Extract title
        title = ""
        img = node.find("img")
        if img and img.get("alt"):
            title = img.get("alt", "").strip()
        if not title and item_link:
            title = item_link.get_text(strip=True)

        order = SalesOrder(
            order_id=order_id,
            item_title=title or f"Order {order_id}",
            sold_price=sold_price,
            sale_date=date.today(),
            platform="Vinted",
            brand=infer_brand(title, text),
            category=infer_clothing_category(title),
        )
        orders.append(order)

    return orders
