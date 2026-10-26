"""Parses transactional emails from Vinted (no-reply@vinted.com) for real-time tracking."""

import re
import base64
import email
import email.message
from email.header import decode_header
import email.utils
import imaplib
import html
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

from src.html_parser import infer_brand, infer_clothing_category


class ParsedEmailEvent(BaseModel):
    # Event types:
    #   "sale" / "bundle_sale"  shipping-label email: one per order, lists every item + Transaction ID
    #   "sale_notice"           "You sold an item": title, price, buyer (no transaction ID)
    #   "completed"             "This order is completed": Transaction ID + final item price
    #   "cancellation"          "Order update for <first item>": order canceled
    #   "order_update"          shipped / delivered / other order-thread status (informational)
    #   "offer", "favorite"     buyer interest on a listing (carry the Vinted item ID)
    #   "ignored"               known Vinted email that is not about your sales (purchases, payouts, ...)
    #   "unknown"               not recognised
    event_type: str = "unknown"
    item_title: Optional[str] = ""
    vinted_id: Optional[str] = ""
    order_id: Optional[str] = ""
    sold_price: float = 0.0
    offer_price: float = 0.0
    listed_price: float = 0.0
    buyer_username: Optional[str] = ""
    vinted_url: Optional[str] = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    raw_subject: Optional[str] = ""
    brand: Optional[str] = ""
    category: Optional[str] = ""
    lot_id: Optional[str] = ""
    source: Optional[str] = "Email"
    quantity: int = 1
    cancellation_reason: Optional[str] = ""
    transaction_id: Optional[str] = ""
    items: List[str] = Field(default_factory=list)
    message_id: Optional[str] = ""
    status: Optional[str] = ""


# ---------------------------------------------------------------------------
# Template-based parsing of real Vinted (US) seller emails
# ---------------------------------------------------------------------------
_PRICE_RE = re.compile(r"\$\s*(\d[\d.,]*)|(\d[\d.,]*)\s*\$")
_INVISIBLE = "\u200c\u200b\u034f\u00a0\ufeff "


def _money(raw: str) -> float:
    """'1,234.50' -> 1234.5 and '3,00' -> 3.0 (comma decimal used by some Vinted locales)."""
    raw = (raw or "").strip().rstrip(".,")
    if re.fullmatch(r"\d+,\d{1,2}", raw):
        raw = raw.replace(",", ".")
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return 0.0


def unwrap_vinted_link(href: str) -> str:
    """Decodes Vinted's click-tracking links (links.vinted.com/t/<base64 url|token>) to the real URL."""
    m = re.search(r"links\.vinted\.[a-z.]+/(?:e/)?t/([A-Za-z0-9_\-=]+)", href or "")
    if not m:
        return href or ""
    b64 = m.group(1)
    try:
        decoded = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8", "ignore")
    except Exception:
        return href
    return decoded.split("|")[0]


def _lines_and_links(content: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Visible text lines and (text, unwrapped url) links of an email body."""
    if "<" in content and ">" in content:
        soup = BeautifulSoup(content, "html.parser")
        for t in soup(["style", "script", "head"]):
            t.decompose()
        links = [(a.get_text(" ", strip=True), unwrap_vinted_link(a["href"])) for a in soup.find_all("a", href=True)]
        text = soup.get_text("\n")
    else:
        links = [("", u) for u in re.findall(r"https?://\S+", content)]
        text = content
    lines = [re.sub(r"\s+", " ", html.unescape(ln)).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln.strip(_INVISIBLE)], links


def _field(lines: List[str], *labels: str) -> List[str]:
    """Values following a 'Label:' line, up to the next 'Something:' line."""
    wanted = {lbl.lower() for lbl in labels}
    for i, ln in enumerate(lines):
        if ln.endswith(":") and ln[:-1].strip().lower() in wanted:
            out = []
            for nxt in lines[i + 1:]:
                if nxt.endswith(":") and len(nxt) < 40:
                    break
                out.append(nxt)
            return out
    return []


def _price(text: str) -> float:
    m = _PRICE_RE.search(text or "")
    return _money(m.group(1) or m.group(2)) if m else 0.0


def _item_id_from_links(links: List[Tuple[str, str]]) -> Tuple[str, str]:
    for _, url in links:
        m = re.search(r"/e/item\?id=(\d+)|/items/(\d+)", url)
        if m:
            vid = m.group(1) or m.group(2)
            return vid, f"https://www.vinted.com/items/{vid}"
    return "", ""


def _conversation_id(links: List[Tuple[str, str]]) -> str:
    for _, url in links:
        m = re.search(r"/inbox/([0-9a-f-]{20,})", url)
        if m:
            return m.group(1)
    return ""


def _event(event_type: str, subject: str, dt: datetime, **kw) -> ParsedEmailEvent:
    title = kw.get("item_title") or ""
    return ParsedEmailEvent(
        event_type=event_type,
        raw_subject=subject,
        timestamp=dt,
        brand=kw.pop("brand", None) or (infer_brand(title, "") if title else ""),
        category=kw.pop("category", None) or (infer_clothing_category(title) if title else ""),
        **kw,
    )


def _parse_label(subject, lines, links, dt):
    m = re.match(r"^(?P<titles>.+?) shipping label\s*[–—-]\s*use by", subject, re.IGNORECASE)
    if not m:
        return None
    items = _field(lines, "Item", "Item name") or [t.strip() for t in m.group("titles").split(", ") if t.strip()]
    txn = re.sub(r"\D", "", (_field(lines, "Transaction ID") or [""])[0])
    if not items:
        return None
    return _event(
        "bundle_sale" if len(items) > 1 else "sale", subject, dt,
        item_title=items[0] if len(items) == 1 else ", ".join(items),
        items=items, quantity=len(items), transaction_id=txn, order_id=txn, status="label_created",
    )


def _parse_sale_notice(subject, lines, links, dt):
    if not re.match(r"^You sold an item", subject, re.IGNORECASE):
        return None
    for i, ln in enumerate(lines):
        if ln.lower() == "has bought" and 0 < i < len(lines) - 1:
            title = lines[i + 1]
            price = next((_price(x) for x in lines[i + 2:i + 4] if _PRICE_RE.search(x)), 0.0)
            return _event(
                "sale_notice", subject, dt, item_title=title, items=[title], sold_price=price,
                buyer_username=lines[i - 1], order_id=_conversation_id(links), status="sold",
            )
    return None


def _parse_completed(subject, lines, links, dt):
    if not re.match(r"^This order is completed", subject, re.IGNORECASE):
        return None
    title_parts, txn = [], ""
    for i, ln in enumerate(lines):
        if ln.lower().startswith("your sale of"):
            rest = ln[len("your sale of"):].strip()
            title_parts = [rest] if rest else []
            for nxt in lines[i + 1:]:
                if nxt.lower().startswith("was completed"):
                    break
                title_parts.append(nxt)
        m = re.match(r"Transaction ID:\s*#?\s*(\d+)", ln, re.IGNORECASE)
        if m:
            txn = m.group(1)
    price = _price(" ".join(_field(lines, "Item price")))
    title = " ".join(title_parts).strip()
    if not txn:
        return None
    return _event(
        "completed", subject, dt, item_title=title, items=[title] if title else [], transaction_id=txn,
        order_id=txn, sold_price=price, status="completed",
    )


def _parse_order_update(subject, lines, links, dt):
    m = re.match(r"^Order update for (?P<title>.+)$", subject, re.IGNORECASE)
    if not m:
        return None
    title = m.group("title").strip()
    body = " ".join(lines[:6])
    conv = _conversation_id(links)
    if re.search(r"has been cancell?ed", body, re.IGNORECASE):
        reason = next((ln for ln in lines if re.search(r"has been cancell?ed", ln, re.IGNORECASE)), "Order canceled")
        return _event(
            "cancellation", subject, dt, item_title=title, items=[title], order_id=conv,
            cancellation_reason=reason, status="canceled",
        )
    if "on its way" in body:
        status = "shipped"
    elif "received their order" in body:
        status = "delivered"
    elif "waiting for" in body and "pick up" in body:
        status = "awaiting_pickup"
    else:
        status = "other"
    qty = re.search(r"bundle of (\d+) items", body)
    return _event(
        "order_update", subject, dt, item_title=title, items=[title], order_id=conv, status=status,
        quantity=int(qty.group(1)) if qty else 1,
    )


def _parse_offer(subject, lines, links, dt):
    m = re.match(r"^New offer for (?P<title>.+)$", subject, re.IGNORECASE)
    if not m:
        return None
    offer, listed = 0.0, 0.0
    for ln in lines:
        mm = re.search(r"(.+?)\s+instead of\s+(.+)", ln)
        if mm and _PRICE_RE.search(mm.group(1)) and _PRICE_RE.search(mm.group(2)):
            offer, listed = _price(mm.group(1)), _price(mm.group(2))
            break
    buyer = next((mm.group(1) for ln in lines for mm in [re.match(r"^@?(\S+?)\s+(?:just offered to buy|sent you an offer|offered|made)", ln)] if mm), "")
    vid, url = _item_id_from_links(links)
    return _event(
        "offer", subject, dt, item_title=m.group("title").strip(), offer_price=offer, listed_price=listed,
        buyer_username=buyer, vinted_id=vid, vinted_url=url,
    )


def _parse_favorite(subject, lines, links, dt):
    m = re.match(r"^Your (?P<title>.+) was just favou?rited", subject, re.IGNORECASE)
    if not m:
        return None
    vid, url = _item_id_from_links(links)
    price = next((_price(ln) for ln in lines if _PRICE_RE.fullmatch(ln.strip())), 0.0)
    return _event(
        "favorite", subject, dt, item_title=m.group("title").strip(), vinted_id=vid, vinted_url=url, listed_price=price,
    )


_IGNORED_SUBJECTS = re.compile(
    r"^(Your receipt for|Leave feedback for the seller|Your payment is being sent|You added and verified|"
    r"Find out what .* thinks of you|New item uploaded by|New message about|Item deleted|Action needed|"
    r"Spreadsheet shared)|Confirmation\s*needed$",
    re.IGNORECASE,
)

_TEMPLATE_PARSERS = (_parse_label, _parse_completed, _parse_sale_notice, _parse_order_update, _parse_offer, _parse_favorite)


def parse_vinted_email(
    subject: str,
    body_text_or_html: str,
    email_date: Optional[datetime] = None,
    message_id: str = "",
) -> ParsedEmailEvent:
    """
    Parses one Vinted notification email.
    Known Vinted templates are matched by subject first (shipping label, completed, sold notice,
    order update, offer, favorite); anything else falls back to the heuristic parser.
    """
    sub_clean = re.sub(r"\s+", " ", subject or "").strip()
    content = body_text_or_html or ""
    dt = email_date or datetime.now()
    lines, links = _lines_and_links(content)
    for parser in _TEMPLATE_PARSERS:
        ev = parser(sub_clean, lines, links, dt)
        if ev is not None:
            ev.message_id = message_id or ""
            return ev
    if _IGNORED_SUBJECTS.search(sub_clean):
        return ParsedEmailEvent(event_type="ignored", raw_subject=sub_clean, timestamp=dt, message_id=message_id or "")
    ev = _legacy_parse_vinted_email(sub_clean, content, dt)
    ev.message_id = message_id or ""
    return ev


def _legacy_parse_vinted_email(subject: str, body_text_or_html: str, email_date: Optional[datetime] = None) -> ParsedEmailEvent:
    """
    Heuristic fallback for emails that don't match a known Vinted template.
    Extracts item title, brand, category, price, buyer, and IDs.
    """
    sub_clean = subject.strip()
    content = body_text_or_html or ""
    dt = email_date or datetime.now()
    soup = BeautifulSoup(content, "html.parser") if ("<" in content and ">" in content) else None

    # 1. Check for Favorite Notification
    if "was just favorited" in sub_clean.lower() or "favorited your item" in content.lower():
        title = ""
        m_title = re.search(r"^(.*?)\s+was just favorited", sub_clean, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip().strip('"').strip("'")

        # Extract Item ID and URL from links in email body
        v_id = ""
        v_url = ""
        slug_title = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-([\w-]+))?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)
            if item_link_match.group(2):
                slug_title = item_link_match.group(2).replace("-", " ").replace("_", " ").title().strip()

        if not title and slug_title:
            title = slug_title

        # Check soup for image alt or link text if title still empty
        if not title and soup:
            for img in soup.find_all("img"):
                alt = (img.get("alt") or "").strip()
                if alt and alt.lower() not in ("vinted", "logo", "star", "avatar", "icon", "arrow"):
                    title = html.unescape(alt).strip().strip('"').strip("'")
                    break

        # Extract buyer username if mentioned (e.g., "@buyer123 added your item")
        buyer = ""
        buyer_match = re.search(r"@([\w.-]+)\s+(?:added|favorited)", content, re.IGNORECASE)
        if buyer_match:
            buyer = buyer_match.group(1)

        brand = infer_brand(title, content)
        category = infer_clothing_category(title) if title else "Other Apparel"

        return ParsedEmailEvent(
            event_type="favorite",
            item_title=title,
            vinted_id=v_id,
            vinted_url=v_url,
            buyer_username=buyer,
            timestamp=dt,
            raw_subject=sub_clean,
            brand=brand,
            category=category,
        )

    # 2. Check for Cancellation / Refund Notification
    is_cancellation = bool(
        re.search(r"(?:order|transaction)\s+(?:was\s+)?(?:cancell?ed|failed|refunded)", sub_clean, re.IGNORECASE)
        or "order cancelled" in sub_clean.lower()
        or "order canceled" in sub_clean.lower()
        or "cancellation confirmation" in sub_clean.lower()
        or "transaction status: failed" in content.lower()
        or "canceled. refund processed" in content.lower()
        or "cancelled. refund processed" in content.lower()
        or re.search(r"(?:order|transaction)\s+(?:has been|was)?\s*(?:cancell?ed|failed|refunded)", content, re.IGNORECASE)
    )

    if is_cancellation:
        # Extract Sold / Refund Price ($X.XX)
        sold_price = 0.0
        price_matches = re.findall(r"\$\s*(\d+(?:\.\d{2})?)", content)
        if price_matches:
            try:
                sold_price = float(price_matches[0])
            except ValueError:
                sold_price = 0.0

        # Extract Item ID, Item URL, and slug title
        v_id = ""
        v_url = ""
        slug_title = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-([\w-]+))?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)
            if item_link_match.group(2):
                slug_title = item_link_match.group(2).replace("-", " ").replace("_", " ").title().strip()

        # Extract Order ID
        order_id = ""
        trans_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/(?:transaction|inbox)/([a-zA-Z0-9-]+)", content)
        if trans_match:
            order_id = trans_match.group(1)
        else:
            order_num_match = re.search(r"order\s*#?\s*:?\s*([a-zA-Z0-9-]{8,})", content, re.IGNORECASE)
            if order_num_match:
                order_id = order_num_match.group(1)
            else:
                order_id = f"ORD-{int(dt.timestamp())}"

        # Extract Buyer (@username)
        buyer = ""
        buyer_match = re.search(r"@([\w.-]+)", content)
        if buyer_match:
            cand_b = buyer_match.group(1).strip()
            if cand_b.lower() not in ("vinted", "support", "admin", "cancellation", "order"):
                buyer = cand_b

        # Cancellation Reason
        cancellation_reason = "Order cancelled. Refund processed."
        m_reason = re.search(r"(?:reason|status):\s*([^\n\r<]+)", content, re.IGNORECASE)
        if m_reason:
            cand_r = m_reason.group(1).strip()
            if len(cand_r) > 3 and not cand_r.lower().startswith("http"):
                cancellation_reason = cand_r
        elif "buyer cancelled" in content.lower() or "buyer canceled" in content.lower():
            cancellation_reason = "Buyer cancelled the order."
        elif "refund processed" in content.lower():
            cancellation_reason = "Canceled. Refund processed."

        # Extract Title
        title = ""
        if soup:
            for img in soup.find_all("img"):
                alt = (img.get("alt") or "").strip()
                if alt:
                    alt_clean = html.unescape(alt).strip().strip('"').strip("'")
                    if (
                        alt_clean.lower() not in (
                            "vinted", "logo", "star", "stars", "avatar", "profile", "badge",
                            "icon", "arrow", "image", "photo", "divider", "spacer", "unnamed", "rating"
                        )
                        and not alt_clean.lower().startswith("http")
                        and len(alt_clean) >= 3
                    ):
                        title = alt_clean
                        break

        # Check explicit "order for <Title> has been/was cancelled"
        if not title:
            m_title_for = re.search(r"order\s+for\s+[\"']?([^\n\r<]+?)[\"']?\s+(?:has been|was)\s+cancell?ed", content, re.IGNORECASE)
            if m_title_for:
                cand = m_title_for.group(1).strip().strip('"').strip("'")
                if cand.lower() not in ("an item", "your item"):
                    title = cand

        # Check item links
        if not title and soup:
            for a in soup.find_all("a", href=True):
                if "/items/" in a["href"]:
                    a_txt = a.get_text(separator=" ", strip=True)
                    if a_txt and len(a_txt) >= 3 and not a_txt.lower().startswith("http") and "view" not in a_txt.lower():
                        title = a_txt
                        break

        if not title:
            title_match = re.search(r"(?:item|article|listing):\s*([^\n\r<]+)", content, re.IGNORECASE)
            if title_match:
                cand = title_match.group(1).strip()
                if cand.lower().startswith("http") or "/items/" in cand:
                    m_slug = re.search(r"/items/\d+-([a-zA-Z0-9_-]+)", cand)
                    cand = m_slug.group(1).replace("-", " ").replace("_", " ").title().strip() if m_slug else ""
                if cand:
                    title = cand

        if not title and slug_title:
            title = slug_title

        if not title:
            m_sub_title = re.search(r"^(.*?)\s+order\s+(?:has been|was)?\s*cancell?ed", sub_clean, re.IGNORECASE)
            if m_sub_title and m_sub_title.group(1).strip():
                cand = m_sub_title.group(1).strip()
                if cand.lower() not in ("your", "the", "an", "an item", "order", "my"):
                    title = cand

        if title:
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip().strip('"').strip("'")

        # Bundle quantity
        quantity = 1
        m_bundle = re.search(r"bundle\s+(\d+)\s+items", (title or "") + " " + content, re.IGNORECASE)
        if m_bundle:
            quantity = int(m_bundle.group(1))

        brand = infer_brand(title, content)
        category = infer_clothing_category(title) if title else "Other Apparel"

        return ParsedEmailEvent(
            event_type="cancellation",
            item_title=title or f"Cancelled Order {order_id}",
            vinted_id=v_id,
            order_id=order_id,
            sold_price=sold_price,
            buyer_username=buyer,
            vinted_url=v_url,
            timestamp=dt,
            raw_subject=sub_clean,
            brand=brand,
            category=category,
            quantity=quantity,
            cancellation_reason=cancellation_reason,
        )

    # 3. Check for Sale Notification
    is_sale = bool(
        re.search(r"(you sold|you've made a sale|you made a sale|item sold|sale confirmation|congratulations on your sale)", sub_clean, re.IGNORECASE)
        or re.search(r"(congratulations on your sale|sold to|buyer bought|you just made a sale|your item has sold)", content, re.IGNORECASE)
    )

    if is_sale:
        # Extract Sold Price ($X.XX)
        sold_price = 0.0
        price_matches = re.findall(r"\$\s*(\d+(?:\.\d{2})?)", content)
        if price_matches:
            try:
                sold_price = float(price_matches[0])
            except ValueError:
                sold_price = 0.0

        # Extract Item ID, Item URL, and slug title
        v_id = ""
        v_url = ""
        slug_title = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-([\w-]+))?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)
            if item_link_match.group(2):
                slug_title = item_link_match.group(2).replace("-", " ").replace("_", " ").title().strip()

        # Extract Order ID / Transaction Reference
        order_id = ""
        trans_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/(?:transaction|inbox)/([a-zA-Z0-9-]+)", content)
        if trans_match:
            order_id = trans_match.group(1)
        else:
            order_num_match = re.search(r"order\s*#?\s*:?\s*([a-zA-Z0-9-]{8,})", content, re.IGNORECASE)
            if order_num_match:
                order_id = order_num_match.group(1)
            else:
                order_id = f"ORD-{int(dt.timestamp())}"

        # Extract Buyer
        buyer = ""
        buyer_match = re.search(r"(?:sold to|bought by|buyer)\s+@?([\w.-]+)", content, re.IGNORECASE)
        if not buyer_match:
            buyer_match = re.search(r"@([\w.-]+)\s+(?:bought|purchased)", content, re.IGNORECASE)
        if buyer_match:
            buyer = buyer_match.group(1).strip()

        # Multi-strategy Title Extraction:
        title = ""

        # Strategy 1: HTML img alt attribute (Vinted always sets product image alt to full listing title)
        if soup:
            for img in soup.find_all("img"):
                alt = (img.get("alt") or "").strip()
                if alt:
                    alt_clean = html.unescape(alt).strip().strip('"').strip("'")
                    if alt_clean.lower() in (
                        "vinted", "logo", "star", "stars", "avatar", "profile", "badge",
                        "icon", "arrow", "image", "photo", "divider", "spacer", "unnamed", "rating"
                    ):
                        continue
                    if alt_clean.lower().startswith("http") or len(alt_clean) < 3:
                        continue
                    title = alt_clean
                    break

        # Fallback regex for img alt if no soup or soup didn't match
        if not title:
            m_alts = re.findall(r'<img\s+[^>]*alt=["\']([^"\']+)["\']', content, re.IGNORECASE)
            for a_cand in m_alts:
                a_cand = html.unescape(a_cand).strip().strip('"').strip("'")
                if a_cand.lower() not in ("vinted", "logo", "star", "stars", "avatar", "profile", "badge", "icon", "arrow", "image", "photo", "divider", "spacer"):
                    if len(a_cand) >= 3 and not a_cand.lower().startswith("http"):
                        title = a_cand
                        break

        # Strategy 2: HTML link text targeting transaction or item (<a href=".../transaction/...">Title</a>)
        if not title and soup:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r"/(?:items|transaction|inbox)/", href):
                    text = a.get_text(separator=" ", strip=True)
                    text_clean = html.unescape(text).strip().strip('"').strip("'")
                    if text_clean and len(text_clean) >= 3 and not text_clean.lower().startswith("http"):
                        if text_clean.lower() not in (
                            "view order details", "order details", "view order", "view item", "see item",
                            "track package", "download shipping label", "download label", "get shipping label",
                            "contact buyer", "message buyer", "help centre", "privacy policy", "terms & conditions",
                            "terms of service", "unsubscribe", "item link", "details", "sold", "item", "order"
                        ):
                            title = text_clean
                            break

        # Strategy 3: Explicit pattern "Item: <Title>" or "Article: <Title>" or "Listing: <Title>"
        if not title:
            title_match = re.search(r"(?:item|article|listing|sold):\s*([^\n\r<]+)", content, re.IGNORECASE)
            if title_match:
                cand = title_match.group(1).strip()
                if cand.lower().startswith("http") or "/items/" in cand:
                    m_slug = re.search(r"/items/\d+-([a-zA-Z0-9_-]+)", cand)
                    cand = m_slug.group(1).replace("-", " ").replace("_", " ").title().strip() if m_slug else ""
                if cand and cand.lower() not in ("sold", "details", "order", "an item", "and item"):
                    title = cand

        # Strategy 4: Subject line "You sold [Title] on Vinted"
        if not title:
            m_sub = re.search(r"you sold\s+[\"']?([^\"'\n\r]+?)[\"']?\s+on vinted", sub_clean, re.IGNORECASE)
            if m_sub and m_sub.group(1).lower() not in ("an item", "and item", "your item"):
                title = m_sub.group(1).strip()

        # Strategy 5: "sold <Title> to @buyer" or "bought <Title>"
        if not title:
            m_sold_to = re.search(r"(?:you sold|sold)\s+[\"']?([^\"'\n\r<]+?)[\"']?\s+to\s+@?[\w.-]+", content, re.IGNORECASE)
            if m_sold_to and m_sold_to.group(1).lower() not in ("an item", "and item", "your item"):
                title = m_sold_to.group(1).strip()

        # Strategy 6: Slug from Item URL
        if not title and slug_title:
            title = slug_title

        # Clean title
        if title:
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip().strip('"').strip("'")

        # Extract Explicit Brand from email body if present
        brand = ""
        m_brand = re.search(r"(?:brand|marque):\s*([^\n\r<·|,]+)", content, re.IGNORECASE)
        if m_brand:
            b_cand = html.unescape(m_brand.group(1)).strip()
            if b_cand.lower() not in ("none", "other", "unbranded", "details", "order"):
                brand = b_cand

        if not brand:
            brand = infer_brand(title, content)

        # Extract Explicit Category from email body if present
        category = ""
        m_cat = re.search(r"(?:category|catégorie):\s*([^\n\r<·|,]+)", content, re.IGNORECASE)
        if m_cat:
            c_cand = infer_clothing_category(html.unescape(m_cat.group(1)).strip())
            if c_cand != "Other Apparel":
                category = c_cand

        if not category:
            category = infer_clothing_category(title) if title else "Other Apparel"

        # Bundle quantity
        quantity = 1
        m_bundle = re.search(r"bundle\s+(\d+)\s+items", (title or "") + " " + content, re.IGNORECASE)
        if m_bundle:
            quantity = int(m_bundle.group(1))
        elif "bundle" in (title or "").lower() or "bundle" in sub_clean.lower():
            m_qty = re.search(r"bundle\s+(?:of\s+)?(\d+)", (title or "") + " " + sub_clean, re.IGNORECASE)
            quantity = int(m_qty.group(1)) if m_qty else 2

        event_type = "bundle_sale" if (quantity > 1 or "bundle" in (title or "").lower() or "bundle" in sub_clean.lower()) else "sale"

        return ParsedEmailEvent(
            event_type=event_type,
            item_title=title,
            vinted_id=v_id,
            order_id=order_id,
            sold_price=sold_price,
            buyer_username=buyer,
            vinted_url=v_url,
            timestamp=dt,
            raw_subject=sub_clean,
            brand=brand,
            category=category,
            quantity=quantity,
        )

    # 4. Check for Offer Received Notification
    is_offer = bool(
        "new offer for" in sub_clean.lower()
        or ("instead of" in content.lower() and re.search(r"\$\s*\d+.*instead of.*\$\s*\d+", content, re.IGNORECASE))
    )

    if is_offer:
        title = ""
        m_title = re.search(r"new offer for\s+(.+)$", sub_clean, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip().strip('"').strip("'")

        # Extract "<offer> instead of <listed price>"
        offer_price = 0.0
        listed_price = 0.0
        m_prices = re.search(r"(\$?\s*\d+(?:\.\d{2})?)\s+instead of\s+(\$?\s*\d+(?:\.\d{2})?)", content, re.IGNORECASE)
        if m_prices:
            raw_off = re.sub(r"[^\d.]", "", m_prices.group(1))
            raw_list = re.sub(r"[^\d.]", "", m_prices.group(2))
            try:
                offer_price = float(raw_off)
            except ValueError:
                pass
            try:
                listed_price = float(raw_list)
            except ValueError:
                pass

        v_id = ""
        v_url = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-[\w-]+)?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)

        buyer = ""
        buyer_match = re.search(r"@([\w.-]+)\s+(?:made|offered|sent)", content, re.IGNORECASE)
        if buyer_match:
            buyer = buyer_match.group(1)

        brand = infer_brand(title, content)
        category = infer_clothing_category(title) if title else "Other Apparel"

        return ParsedEmailEvent(
            event_type="offer",
            item_title=title,
            offer_price=offer_price,
            listed_price=listed_price,
            vinted_id=v_id,
            vinted_url=v_url,
            buyer_username=buyer,
            timestamp=dt,
            raw_subject=sub_clean,
            brand=brand,
            category=category,
        )

    return ParsedEmailEvent(
        event_type="unknown",
        raw_subject=sub_clean,
        timestamp=dt,
    )


def decode_mime_words(s: Optional[str]) -> str:
    """Safely decodes RFC 2047 MIME-encoded headers."""
    if not s:
        return ""
    decoded = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                decoded.append(part.decode("latin-1", errors="ignore"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


def extract_email_body(msg: email.message.Message) -> str:
    """Returns the HTML body of an email (plain text only if there is no HTML part), never both."""
    found: Dict[str, str] = {}
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        cdispo = str(part.get("Content-Disposition") or "")
        if "attachment" in cdispo or ctype not in ("text/plain", "text/html") or ctype in found:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            charset = part.get_content_charset() or "utf-8"
            try:
                found[ctype] = payload.decode(charset, errors="ignore")
            except Exception:
                found[ctype] = payload.decode("latin-1", errors="ignore")
    return found.get("text/html") or found.get("text/plain") or ""


class EmailAuthError(RuntimeError):
    """Raised when Gmail rejects the IMAP login (wrong address / app password, IMAP disabled)."""


def _to_local_naive(dt: datetime) -> datetime:
    """Converts an email Date header (usually UTC) to local wall-clock time so sale dates don't shift a day."""
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _resolve_imap_folder(mail: imaplib.IMAP4_SSL, folder: str = "ALL") -> str:
    """
    Selects the requested IMAP mailbox.
    If 'ALL' is specified, discovers Gmail's 'All Mail' mailbox (which includes archived emails)
    or falls back gracefully to 'INBOX'.
    Strips surrounding quotes to prevent double-quoting errors in imaplib.
    """
    if folder.upper() == "INBOX":
        mail.select("INBOX")
        return "INBOX"

    candidates = ["[Gmail]/All Mail", "[Google Mail]/All Mail", "INBOX"]
    try:
        status, folder_list = mail.list()
        if status == "OK" and folder_list:
            for item in folder_list:
                item_str = item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
                if r"\All" in item_str:
                    parts = item_str.split(' "/" ')
                    if len(parts) == 2:
                        raw_name = parts[1].strip().strip('"').strip("'")
                        if raw_name and raw_name not in candidates:
                            candidates.insert(0, raw_name)
    except Exception:
        pass

    for candidate in candidates:
        clean_candidate = candidate.strip().strip('"').strip("'")
        try:
            status, _ = mail.select(clean_candidate)
            if status == "OK":
                return clean_candidate
        except Exception:
            continue

    mail.select("INBOX")
    return "INBOX"


def get_last_scan_date() -> Optional[date]:
    """
    Retrieves the date of the last successful email sync from .sync_status.json.
    Returns None if sync has not been run yet.
    """
    try:
        from src.notifications import get_sync_status
        status = get_sync_status()
        raw = status.get("last_email_parsed_at", "")
        if raw and str(raw).strip().lower() not in ("not yet run", "none", ""):
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(str(raw).strip()[:19], fmt).date()
                except Exception:
                    pass
    except Exception:
        pass
    return None


def fetch_vinted_emails_via_imap(
    username: str,
    password: str,
    folder: str = "ALL",
    max_emails: int = 2000,
    search_query: Optional[str] = None,
    scan_processed_label: bool = True,
    since_date: Optional[Union[date, datetime]] = None,
    until_date: Optional[Union[date, datetime]] = None,
    raise_on_auth_error: bool = False,
    include_ignored: bool = False,
) -> List[ParsedEmailEvent]:
    """
    Connects to Gmail via IMAP (read-only) and parses transactional emails from Vinted.
    By default, scans 'ALL' mail (including archived, inbox, and labeled messages).
    Supports date range filtering with since_date and until_date.
    Uses Gmail native X-GM-RAW search criteria when available with standard IMAP fallback.
    Returns ParsedEmailEvent objects (oldest first) with local-time timestamps and Message-IDs.
    With raise_on_auth_error=True a rejected login raises EmailAuthError instead of returning [].
    """
    if not username or not password:
        return []

    if isinstance(since_date, datetime):
        since_date = since_date.date()
    if isinstance(until_date, datetime):
        until_date = until_date.date()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(username.strip(), password.replace(" ", "").strip())
    except Exception as e:
        print(f"[IMAP Error] Failed to connect/login to Gmail ({username}): {e}")
        if raise_on_auth_error:
            raise EmailAuthError(
                f"Gmail rejected the login for {username}. Check the address and 16-character App Password "
                f"(2-Step Verification must be on). Details: {e}"
            ) from e
        return []

    _resolve_imap_folder(mail, folder)

    collected_msg_ids: List[bytes] = []
    seen_ids = set()

    def _add_ids(raw_data):
        if raw_data and raw_data[0]:
            for mid in raw_data[0].split():
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    collected_msg_ids.append(mid)

    # Date filter strings
    gm_date_clause = ""
    if since_date:
        gm_date_clause += f" after:{since_date.strftime('%Y/%m/%d')}"
    if until_date:
        gm_date_clause += f" before:{(until_date + timedelta(days=1)).strftime('%Y/%m/%d')}"

    # 1. If scan_processed_label is True, search for label:Vinted-Processed using Gmail X-GM-RAW
    if scan_processed_label:
        for lbl_q in ('label:Vinted-Processed', 'label:"Vinted Processed"'):
            try:
                full_lbl = f"{lbl_q}{gm_date_clause}"
                status, search_data = mail.search(None, 'X-GM-RAW', full_lbl)
                if status == "OK":
                    _add_ids(search_data)
            except Exception:
                pass

    # 2. Search for Vinted emails
    if search_query:
        try:
            status, search_data = mail.search(None, search_query)
            if status == "OK":
                _add_ids(search_data)
        except Exception:
            pass
    else:
        # Strategy A: Gmail native search scoped to Vinted senders (matches inbox, archived, labels)
        raw_gmail_queries = [
            f"from:vinted.com{gm_date_clause}",
        ]
        for q in raw_gmail_queries:
            try:
                status, search_data = mail.search(None, 'X-GM-RAW', q)
                if status == "OK":
                    _add_ids(search_data)
            except Exception:
                pass

        # Strategy B: Fallback standard IMAP search if X-GM-RAW yielded no results
        if not collected_msg_ids:
            try:
                since_clause = f'SINCE "{since_date.strftime("%d-%b-%Y")}" ' if since_date else ""
                for fallback_crit in (f'({since_clause}FROM "vinted")', f'({since_clause}SUBJECT "vinted")'):
                    try:
                        status, search_data = mail.search(None, fallback_crit)
                        if status == "OK":
                            _add_ids(search_data)
                    except Exception:
                        pass
            except Exception:
                pass

    # 3. Check dedicated IMAP folder "Vinted-Processed" if empty
    if not collected_msg_ids and scan_processed_label:
        try:
            status, folder_list = mail.list()
            if status == "OK" and folder_list:
                for item in folder_list:
                    item_str = item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
                    if "Vinted-Processed" in item_str or "vinted-processed" in item_str.lower():
                        parts = item_str.split(' "/" ')
                        if len(parts) == 2:
                            custom_box = parts[1].strip().strip('"').strip("'")
                            s_status, _ = mail.select(custom_box)
                            if s_status == "OK":
                                st_all, d_all = mail.search(None, "ALL")
                                if st_all == "OK":
                                    _add_ids(d_all)
                                    break
        except Exception:
            pass

    if not collected_msg_ids:
        try:
            mail.logout()
        except Exception:
            pass
        return []

    # Sort message IDs numerically to preserve chronological order (most recent at end)
    try:
        collected_msg_ids.sort(key=lambda x: int(x))
    except Exception:
        pass

    target_ids = collected_msg_ids[-max_emails:]
    events: List[ParsedEmailEvent] = []

    # Batched, read-only fetch (BODY.PEEK keeps emails unread)
    for start in range(0, len(target_ids), 50):
        chunk = target_ids[start:start + 50]
        try:
            res, data = mail.fetch(b",".join(chunk), "(BODY.PEEK[])")
        except Exception as e:
            print(f"[IMAP Warning] Error fetching messages {chunk[0]}..{chunk[-1]}: {e}")
            continue
        if res != "OK" or not data:
            continue
        for response_part in data:
            if not isinstance(response_part, tuple):
                continue
            try:
                msg = email.message_from_bytes(response_part[1])
                subject = decode_mime_words(msg.get("Subject", ""))
                date_header = msg.get("Date")
                dt = _to_local_naive(email.utils.parsedate_to_datetime(date_header)) if date_header else datetime.now()
                body = extract_email_body(msg)
                event = parse_vinted_email(subject, body, email_date=dt, message_id=(msg.get("Message-ID") or "").strip())
                if event.event_type == "unknown" or (event.event_type == "ignored" and not include_ignored):
                    continue
                events.append(event)
            except Exception as e:
                print(f"[IMAP Warning] Error parsing message: {e}")

    try:
        mail.logout()
    except Exception:
        pass

    events.sort(key=lambda e: e.timestamp)
    return events


