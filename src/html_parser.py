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
    if any(k in t for k in ["jacket", "coat", "blazer", "parka", "windbreaker", "vest", "anorak", "overcoat", "bomber", "trench"]):
        return "Outerwear"
    if any(k in t for k in ["sweater", "hoodie", "sweatshirt", "cardigan", "pullover", "fleece", "knit", "jumper", "crewneck", "quarter zip"]):
        return "Sweaters & Hoodies"
    if any(k in t for k in ["shirt", "tee", "top", "blouse", "polo", "tank", "jersey", "t-shirt", "button up", "button down", "henley", "camisole", "crop"]):
        return "Tops & Shirts"
    if any(k in t for k in ["jean", "pant", "trouser", "denim", "short", "legging", "jogger", "sweatpant", "cargo", "chino", "bottom"]):
        return "Pants & Jeans"
    if any(k in t for k in ["shoe", "boot", "sneaker", "heel", "sandal", "loafer", "flat", "cleat", "clog", "slide", "mule", "slipper"]):
        return "Footwear"
    if any(k in t for k in ["dress", "skirt", "gown", "sundress", "maxi", "midi", "mini"]):
        return "Dresses & Skirts"
    if any(k in t for k in ["bag", "purse", "backpack", "tote", "hat", "cap", "belt", "scarf", "sunglass", "wallet", "jewelry", "necklace", "beanie", "ring", "bracelet", "watch", "tie"]):
        return "Accessories"
    if any(k in t for k in ["swim", "bikini", "trunks", "boardshort"]):
        return "Swimwear"
    if any(k in t for k in ["bra", "underwear", "boxer", "brief", "sock", "pajama", "robe", "lounge"]):
        return "Underwear & Loungewear"
    return "Other Apparel"


def infer_brand(title: str, card_text: str = "") -> str:
    """Detects brand from card text or garment title."""
    popular_brands = [
        "Carhartt", "Nike", "Levi's", "Levis", "Levi", "Adidas", "Ralph Lauren", "Polo Ralph Lauren", "Polo",
        "The North Face", "North Face", "Patagonia", "Stussy", "Champion",
        "Harley Davidson", "Dickies", "Tommy Hilfiger", "Wrangler", "L.L. Bean", "LL Bean",
        "Arc'teryx", "Arcteryx", "Supreme", "Zara", "H&M", "Columbia", "Coach",
        "Free People", "Abercrombie", "American Eagle", "BCBG", "Calvin Klein",
        "Lululemon", "Under Armour", "Puma", "Reebok", "New Balance", "Vans", "Converse",
        "GAP", "Old Navy", "Banana Republic", "J.Crew", "Madewell", "Urban Outfitters",
        "Hollister", "Aeropostale", "Gucci", "Prada", "Louis Vuitton", "Burberry",
        "Bape", "Palace", "Kith", "Off-White", "Fear of God", "Essentials",
        "Timberland", "Dr. Martens", "Birkenstock", "UGG", "Doc Martens",
        "Guess", "Express", "Lucky Brand", "True Religion", "Diesel", "G-Star",
        "Michael Kors", "Kate Spade", "Tory Burch", "Marc Jacobs", "Steve Madden",
        "Disney", "Starter", "Russell Athletic", "Fruit of the Loom", "Gildan",
        "Hanes", "Jerzees", "Brooks Brothers", "Pendleton", "Woolrich", "Filson",
        "Barbour", "Lowe Alpine", "Marmot", "Mountain Hardwear", "Salomon"
    ]
    combined = f"{title} {card_text}".lower()
    for brand in popular_brands:
        b_lower = brand.lower()
        if re.search(r"\b" + re.escape(b_lower) + r"\b", combined) or b_lower in combined:
            if b_lower in ("levis", "levi"):
                return "Levi's"
            if b_lower in ("polo", "polo ralph lauren"):
                return "Ralph Lauren"
            if b_lower == "ll bean":
                return "L.L. Bean"
            if b_lower == "north face":
                return "The North Face"
            if b_lower == "arcteryx":
                return "Arc'teryx"
            if b_lower == "doc martens":
                return "Dr. Martens"
            return brand
    return ""


def detect_html_type(content: Union[str, bytes]) -> str:
    """
    Detects whether uploaded Vinted HTML content is:
    - 'orders': My orders / sales transaction history
    - 'sold_closet': Public or member profile sold items tab
    - 'active_closet': Public or member profile active listings tab
    - 'unknown': Unrecognized HTML
    """
    text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)

    if "my-orders-item" in text or 'data-testid="my-orders-item"' in text or "/inbox/01" in text:
        return "orders"

    has_items = bool(re.search(r"/items/\d+", text) or "product-item-id-" in text)
    if not has_items:
        return "unknown"

    sold_count = len(re.findall(r"\b(?:Sold|Closed)\b", text, re.IGNORECASE))
    bump_count = len(re.findall(r"\bBump\b", text, re.IGNORECASE))

    if "member profile _ Vinted_sold" in text or (sold_count > 10 and bump_count == 0):
        return "sold_closet"
    return "active_closet"


def parse_closet_html(html_content_or_path: Union[str, Path, bytes], default_lot: str = "") -> List[ItemListing]:
    """
    Parses a saved Vinted closet/profile HTML file, string, or bytes.
    Extracts all listings with title, asking price, Vinted ID, favorites, views, and status.
    """
    if isinstance(html_content_or_path, bytes):
        html = html_content_or_path.decode("utf-8", errors="ignore")
    elif isinstance(html_content_or_path, Path) or (isinstance(html_content_or_path, str) and "\n" not in html_content_or_path and Path(html_content_or_path).exists()):
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
        cards = soup.select("div[data-testid*='product-item-id-']")
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

        # Extract title and brand from img alt if formatted like "Title, brand: Brand, condition: ..."
        img = card.find("img")
        raw_alt = (img.get("alt") if img else "") or link.get("title", "") or ""

        title = ""
        brand = ""
        if "brand:" in raw_alt:
            m = re.match(r"^(.*?),\s*brand:\s*([^,]+)", raw_alt)
            if m:
                title = m.group(1).strip()
                brand = m.group(2).strip()

        if not title:
            if raw_alt and "brand:" not in raw_alt:
                title = raw_alt.split(",")[0].strip()
            else:
                slug_match = re.search(r"/items/\d+-([a-zA-Z0-9-]+)", href)
                if slug_match:
                    title = slug_match.group(1).replace("-", " ").title()

        # Extract price
        price_match = re.search(r"\$\s*(\d+(?:\.\d{2})?)", text) or re.search(r"\$\s*(\d+(?:\.\d{2})?)", raw_alt)
        price_val = float(price_match.group(1)) if price_match else 0.0

        # Extract favorites
        fav_match = re.search(r"(\d+)\s*(?:favou?rites?|likes?)", text, re.IGNORECASE)
        fav_count = int(fav_match.group(1)) if fav_match else 0

        # Extract views
        views_match = re.search(r"(\d+)\s*views?", text, re.IGNORECASE)
        views_count = int(views_match.group(1)) if views_match else 0

        # Check status
        is_sold = bool(re.search(r"\b(?:sold|closed)\b", text, re.IGNORECASE))
        status = "Sold" if is_sold else "Listed"

        if not brand:
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
            views=views_count,
            vinted_id=v_id,
            vinted_url=full_url,
            status=status,
            date_listed=date.today(),
            last_synced=datetime.now(),
        )
        items.append(listing)

    return items


def parse_orders_html(html_content_or_path: Union[str, Path, bytes]) -> List[SalesOrder]:
    """
    Parses a saved Vinted 'My orders' / sales history HTML page.
    Extracts Order IDs, item titles, sold prices, and sale dates.
    """
    if isinstance(html_content_or_path, bytes):
        html = html_content_or_path.decode("utf-8", errors="ignore")
    elif isinstance(html_content_or_path, Path) or (isinstance(html_content_or_path, str) and "\n" not in html_content_or_path and Path(html_content_or_path).exists()):
        with open(html_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    else:
        html = str(html_content_or_path)

    soup = BeautifulSoup(html, "html.parser")
    orders: List[SalesOrder] = []
    seen_order_ids = set()

    # Look for order cards or links
    order_elements = soup.find_all("a", attrs={"data-testid": "my-orders-item"})
    if not order_elements:
        order_elements = soup.find_all("a", href=re.compile(r"/(?:transaction|inbox)/([a-zA-Z0-9-]+)"))

    for elem in order_elements:
        node = elem.parent if elem.parent and elem.parent.name in ("div", "li") and ("order" in elem.parent.get("class", [""])[0] or "transaction" in elem.parent.get("class", [""])[0]) else elem
        href = elem.get("href", "")
        m = re.search(r"/(?:transaction|inbox)/([a-zA-Z0-9-]+)", href)
        if not m:
            continue
        order_id = m.group(1)
        if order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)

        # Extract text and child elements
        strings = list(elem.stripped_strings)
        title = ""
        sold_price = 0.0

        for s in strings:
            if re.search(r"^\$\s*\d+", s):
                p_m = re.search(r"\$\s*(\d+(?:\.\d{2})?)", s)
                if p_m:
                    sold_price = float(p_m.group(1))
            elif not title and not s.lower().startswith("transaction") and not s.lower().startswith("order") and not "shipping label" in s.lower() and not "tracking" in s.lower() and not "delivered" in s.lower() and not "cancelled" in s.lower():
                title = s

        # Item link if present always provides the specific product title
        item_link = node.find("a", href=re.compile(r"/items/"))
        if item_link and item_link.get_text(strip=True):
            title = item_link.get_text(strip=True)

        if not sold_price:
            all_text = node.get_text(separator=" ", strip=True)
            p_m = re.search(r"\$\s*(\d+(?:\.\d{2})?)", all_text)
            if p_m:
                sold_price = float(p_m.group(1))

        if not title and len(strings) > 1:
            title = strings[1]

        order = SalesOrder(
            order_id=order_id,
            item_title=title or f"Order {order_id}",
            sold_price=sold_price,
            sale_date=date.today(),
            platform="Vinted",
            brand=infer_brand(title, " ".join(strings)),
            category=infer_clothing_category(title),
        )
        orders.append(order)

    return orders
