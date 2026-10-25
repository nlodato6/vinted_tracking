"""HTML Parser for locally saved Vinted pages (closet and orders)."""

import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union, Tuple
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


def detect_html_type(content: Union[str, bytes], filename: str = "") -> str:
    """
    Detects whether uploaded Vinted HTML content is:
    - 'orders': My orders / sales transaction history
    - 'sold_closet': Public or member profile sold items tab
    - 'active_closet': Public or member profile active listings tab
    - 'unknown': Unrecognized HTML
    """
    fn_lower = filename.lower()
    if fn_lower:
        if any(k in fn_lower for k in ("order", "orders", "transaction", "sales")):
            return "orders"
        if any(k in fn_lower for k in ("sold", "closed")):
            return "sold_closet"
        if any(k in fn_lower for k in ("active", "wardrobe", "listings", "listing")):
            return "active_closet"

    text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)

    if "my-orders-item" in text or 'data-testid="my-orders-item"' in text or "/inbox/01" in text:
        return "orders"

    has_items = bool(re.search(r"/items/\d+", text) or "product-item-id-" in text)
    if not has_items:
        return "unknown"

    sold_count = len(re.findall(r"\b(?:Sold|Closed)\b", text, re.IGNORECASE))
    bump_count = len(re.findall(r"\bBump\b", text, re.IGNORECASE))

    if (
        "member profile _ vinted_sold" in text.lower()
        or "_sold" in text.lower()[:1000]
        or bool(re.search(r"<title>[^<]*(?:sold|closed)[^<]*</title>", text, re.IGNORECASE))
        or (sold_count > 15 and bump_count == 0)
    ):
        return "sold_closet"
    return "active_closet"


def parse_closet_html(
    html_content_or_path: Union[str, Path, bytes],
    default_lot: str = "",
    filter_sold_only: Optional[bool] = None,
    filename: str = "",
) -> List[ItemListing]:
    """
    Parses a saved Vinted closet/profile HTML file, string, or bytes.
    Extracts all listings with title, asking price, Vinted ID, favorites, views, and status.
    Handles 'Listed', 'Sold', 'Hidden', and 'Reserved' status badges.
    If filter_sold_only is True (or auto-detected from a sold_closet export), treats items as sold.
    """
    if isinstance(html_content_or_path, bytes):
        html = html_content_or_path.decode("utf-8", errors="ignore")
    elif isinstance(html_content_or_path, Path) or (isinstance(html_content_or_path, str) and "\n" not in html_content_or_path and Path(html_content_or_path).exists()):
        with open(html_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    else:
        html = str(html_content_or_path)

    detected_type = detect_html_type(html, filename=filename)
    should_filter_sold = filter_sold_only if filter_sold_only is not None else (detected_type == "sold_closet")
    is_sold_closet_export = (filter_sold_only is True) or (detected_type == "sold_closet" and filter_sold_only is not False)

    soup = BeautifulSoup(html, "html.parser")
    items: List[ItemListing] = []
    seen_ids = set()

    # Look for item cards via all known Vinted card selectors
    cards = soup.select(
        "[data-testid*='grid-item'], div[class*='feed-grid__item'], "
        "div[class*='ItemBox_item-box'], div[class*='web_ui__ItemBox__box'], "
        "div[data-testid*='product-item-id-']"
    )
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

        # Check status (Sold, Hidden, Reserved, or Listed)
        status_elem = card.select_one('[data-testid*="status"], [class*="status"], [class*="badge"], [class*="overlay"]')
        st_text = status_elem.get_text(" ", strip=True).lower() if status_elem else ""

        # Check for standalone status tags (e.g. <span>Sold</span> or <p>Closed</p>)
        standalone_badges = {
            child.get_text(strip=True).lower()
            for child in card.find_all(["span", "div", "p", "b", "strong"])
            if child.get_text(strip=True).lower() in ("sold", "closed", "hidden", "reserved", "réservé", "vendu")
        }

        if any(k in st_text for k in ("sold", "closed", "vendu")) or "sold" in standalone_badges or "closed" in standalone_badges or "vendu" in standalone_badges:
            status = "Sold"
        elif "hidden" in st_text or "hidden" in standalone_badges:
            status = "Hidden"
        elif any(k in st_text for k in ("reserved", "réservé")) or "reserved" in standalone_badges or "réservé" in standalone_badges:
            status = "Reserved"
        else:
            status = "Listed"

        if should_filter_sold and status == "Listed":
            continue

        is_sold = status == "Sold"

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


def parse_orders_html(
    html_content_or_path: Union[str, Path, bytes],
    filter_cancelled: bool = False,
) -> List[SalesOrder]:
    """
    Parses a saved Vinted 'My orders' / sales history HTML page.
    Extracts Order IDs, item titles, sold prices, sale dates, transaction statuses,
    bundle quantities, and cancellation reasons.
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
        vinted_status = "Completed"
        status_details = ""

        for s in strings:
            s_lower = s.lower()
            if s_lower.startswith("transaction status:"):
                raw_st = s.split(":", 1)[1].strip()
                if raw_st.lower() in ("failed", "canceled", "cancelled"):
                    vinted_status = "Cancelled"
                elif raw_st.lower() in ("actions required", "in progress"):
                    vinted_status = "In Progress"
                elif raw_st.lower() == "completed":
                    vinted_status = "Completed"
                else:
                    vinted_status = raw_st
            elif any(k in s_lower for k in ("refund processed", "canceled", "cancelled", "shipping label", "buyer accepted", "order completed")):
                status_details = s.strip()
                if "refund" in s_lower or "canceled" in s_lower or "cancelled" in s_lower:
                    vinted_status = "Cancelled"

            if re.search(r"^\$\s*\d+", s):
                p_m = re.search(r"\$\s*(\d+(?:\.\d{2})?)", s)
                if p_m:
                    sold_price = float(p_m.group(1))
            elif (
                not title
                and not s_lower.startswith("transaction")
                and not s_lower.startswith("order")
                and not "shipping label" in s_lower
                and not "tracking" in s_lower
                and not "delivered" in s_lower
                and not "cancelled" in s_lower
                and not "canceled" in s_lower
                and not "refund" in s_lower
            ):
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

        # Extract bundle quantity
        quantity = 1
        m_bundle = re.search(r"bundle\s+(\d+)\s+items", title, re.IGNORECASE)
        if m_bundle:
            quantity = int(m_bundle.group(1))
        else:
            m_pack = re.search(r"\((\d+)\)", title)
            if m_pack:
                try:
                    quantity = int(m_pack.group(1))
                except ValueError:
                    quantity = 1

        if filter_cancelled and vinted_status == "Cancelled":
            continue

        order = SalesOrder(
            order_id=order_id,
            item_title=title or f"Order {order_id}",
            sold_price=sold_price,
            sale_date=date.today(),
            platform="Vinted",
            brand=infer_brand(title, " ".join(strings)),
            category=infer_clothing_category(title),
            vinted_status=vinted_status,
            quantity=quantity,
            cancellation_reason=status_details,
        )
        orders.append(order)

    return orders


def parse_orders_and_cancellations(
    html_content_or_path: Union[str, Path, bytes]
) -> Tuple[List[SalesOrder], List[SalesOrder]]:
    """
    Parses orders HTML and splits orders into (completed_or_in_progress, cancelled).
    """
    all_orders = parse_orders_html(html_content_or_path, filter_cancelled=False)
    valid_sales = [o for o in all_orders if o.vinted_status != "Cancelled"]
    cancelled = [o for o in all_orders if o.vinted_status == "Cancelled"]
    return valid_sales, cancelled
