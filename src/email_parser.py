"""Parses transactional emails from Vinted (no-reply@vinted.com) for real-time tracking."""

import re
import email
from email.header import decode_header
import email.utils
import imaplib
import html
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from bs4 import BeautifulSoup

from src.html_parser import infer_brand, infer_clothing_category


class ParsedEmailEvent(BaseModel):
    event_type: str = "unknown"  # "sale", "favorite", "offer", or "unknown"
    item_title: Optional[str] = ""
    vinted_id: Optional[str] = ""
    order_id: Optional[str] = ""
    sold_price: float = 0.0
    offer_price: float = 0.0
    listed_price: float = 0.0
    buyer_username: Optional[str] = ""
    vinted_url: Optional[str] = ""
    timestamp: datetime = datetime.now()
    raw_subject: Optional[str] = ""
    brand: Optional[str] = ""
    category: Optional[str] = ""
    lot_id: Optional[str] = ""
    source: Optional[str] = "Email"



def parse_vinted_email(subject: str, body_text_or_html: str, email_date: Optional[datetime] = None) -> ParsedEmailEvent:
    """
    Parses Vinted notification email content without web scraping.
    Handles sale confirmation emails, favorite notification emails, and offers.
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

    # 2. Check for Sale Notification
    is_sale = bool(
        re.search(r"(you sold|you've made a sale|you made a sale|item sold|sale confirmation|congratulations on your sale)", sub_clean, re.IGNORECASE)
        or re.search(r"(congratulations on your sale|sold to|buyer bought|you just made a sale|your item has sold|order details)", content, re.IGNORECASE)
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

        return ParsedEmailEvent(
            event_type="sale",
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
        )

    # 3. Check for Offer Received Notification
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
    """Extracts plain text or HTML body from an email Message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            if "attachment" not in cdispo and ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body += payload.decode(charset, errors="ignore") + "\n"
                    except Exception:
                        body += payload.decode("latin-1", errors="ignore") + "\n"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, errors="ignore")
            except Exception:
                body = payload.decode("latin-1", errors="ignore")
    return body


def _resolve_imap_folder(mail: imaplib.IMAP4_SSL, folder: str = "ALL") -> str:
    """
    Selects the requested IMAP mailbox.
    If 'ALL' is specified, discovers Gmail's 'All Mail' mailbox (which includes archived emails)
    or falls back gracefully to 'INBOX'.
    """
    if folder.upper() == "INBOX":
        mail.select("INBOX")
        return "INBOX"

    candidates = ['"[Gmail]/All Mail"', '"[Google Mail]/All Mail"', 'INBOX']
    try:
        status, folder_list = mail.list()
        if status == "OK" and folder_list:
            for item in folder_list:
                if isinstance(item, bytes):
                    item_str = item.decode("utf-8", errors="ignore")
                    if r"\All" in item_str:
                        parts = item_str.split(' "/" ')
                        if len(parts) == 2:
                            candidates.insert(0, parts[1].strip())
    except Exception:
        pass

    for candidate in candidates:
        try:
            status, _ = mail.select(candidate)
            if status == "OK":
                return candidate
        except Exception:
            continue

    mail.select("INBOX")
    return "INBOX"


def fetch_vinted_emails_via_imap(
    username: str,
    password: str,
    folder: str = "ALL",
    max_emails: int = 250,
    search_query: Optional[str] = None,
    scan_processed_label: bool = True,
) -> List[ParsedEmailEvent]:
    """
    Connects to Gmail via IMAP and parses transactional emails from Vinted.
    By default, scans 'ALL' mail (including archived, inbox, and labeled messages).
    Specifically searches for messages tagged with 'Vinted-Processed' and messages from Vinted.
    Returns list of ParsedEmailEvent objects.
    """
    if not username or not password:
        return []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(username, password)
    except Exception as e:
        print(f"[IMAP Error] Failed to connect/login to Gmail ({username}): {e}")
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

    # 1. If scan_processed_label is True, search for label:Vinted-Processed using Gmail X-GM-RAW
    if scan_processed_label:
        for lbl_q in ('label:Vinted-Processed', 'label:"Vinted Processed"'):
            try:
                status, search_data = mail.search(None, 'X-GM-RAW', lbl_q)
                if status == "OK":
                    _add_ids(search_data)
            except Exception:
                pass

    # 2. Search for all Vinted emails
    if search_query:
        try:
            status, search_data = mail.search(None, search_query)
            if status == "OK":
                _add_ids(search_data)
        except Exception:
            pass
    else:
        # Try X-GM-RAW from:vinted
        try:
            status, search_data = mail.search(None, 'X-GM-RAW', 'from:vinted')
            if status == "OK":
                _add_ids(search_data)
        except Exception:
            pass

        # Fallback standard IMAP search (FROM "vinted")
        try:
            status, search_data = mail.search(None, '(FROM "vinted")')
            if status == "OK":
                _add_ids(search_data)
        except Exception:
            pass

    # 3. If no emails found yet and scan_processed_label is True, check if a dedicated IMAP folder "Vinted-Processed" exists
    if not collected_msg_ids and scan_processed_label:
        try:
            status, folder_list = mail.list()
            if status == "OK" and folder_list:
                for item in folder_list:
                    item_str = item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
                    if "Vinted-Processed" in item_str or "vinted-processed" in item_str.lower():
                        parts = item_str.split(' "/" ')
                        if len(parts) == 2:
                            custom_box = parts[1].strip()
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

    for m_id in target_ids:
        try:
            res, data = mail.fetch(m_id, "(RFC822)")
            if res != "OK" or not data:
                continue
            for response_part in data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg.get("Subject", ""))
                    date_header = msg.get("Date")
                    dt = email.utils.parsedate_to_datetime(date_header) if date_header else datetime.now()
                    body = extract_email_body(msg)
                    event = parse_vinted_email(subject, body, email_date=dt)
                    if event.event_type != "unknown":
                        events.append(event)
        except Exception as e:
            print(f"[IMAP Warning] Error fetching message {m_id}: {e}")
            continue

    try:
        mail.logout()
    except Exception:
        pass

    return events


