"""Parses transactional emails from Vinted (no-reply@vinted.com) for real-time tracking."""

import re
from datetime import datetime, date
from typing import Optional, Dict, Any
from pydantic import BaseModel


class ParsedEmailEvent(BaseModel):
    event_type: str  # "sale", "favorite", "offer", or "unknown"
    item_title: str = ""
    vinted_id: str = ""
    order_id: str = ""
    sold_price: float = 0.0
    offer_price: float = 0.0
    listed_price: float = 0.0
    buyer_username: str = ""
    vinted_url: str = ""
    timestamp: datetime = datetime.now()
    raw_subject: str = ""



def parse_vinted_email(subject: str, body_text_or_html: str, email_date: Optional[datetime] = None) -> ParsedEmailEvent:
    """
    Parses Vinted notification email content without web scraping.
    Handles both sale confirmation emails and favorite notification emails.
    """
    sub_clean = subject.strip()
    content = body_text_or_html or ""
    dt = email_date or datetime.now()

    # 1. Check for Favorite Notification
    if "was just favorited" in sub_clean.lower() or "favorited your item" in content.lower():
        # Title usually precedes "was just favorited" in the subject line
        # e.g., 'Face Print Pants was just favorited' -> 'Face Print Pants'
        title = ""
        m_title = re.search(r"^(.*?)\s+was just favorited", sub_clean, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip().strip('"').strip("'")

        # Extract Item ID and URL from links in email body
        v_id = ""
        v_url = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-[\w-]+)?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)

        # Extract buyer username if mentioned (e.g., "@buyer123 added your item")
        buyer = ""
        buyer_match = re.search(r"@([\w.-]+)\s+(?:added|favorited)", content, re.IGNORECASE)
        if buyer_match:
            buyer = buyer_match.group(1)

        return ParsedEmailEvent(
            event_type="favorite",
            item_title=title,
            vinted_id=v_id,
            vinted_url=v_url,
            buyer_username=buyer,
            timestamp=dt,
            raw_subject=sub_clean,
        )

    # 2. Check for Sale Notification
    is_sale = bool(
        re.search(r"(you sold an item|you sold and item|you've made a sale|item sold)", sub_clean, re.IGNORECASE)
        or re.search(r"(congratulations on your sale|sold to|buyer bought)", content, re.IGNORECASE)
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

        # Extract Item ID and Item URL
        v_id = ""
        v_url = ""
        item_link_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:-[\w-]+)?", content)
        if item_link_match:
            v_id = item_link_match.group(1)
            v_url = item_link_match.group(0)

        # Extract Order ID / Transaction Reference
        order_id = ""
        trans_match = re.search(r"https?://(?:www\.)?vinted\.[a-z.]+/(?:transaction|inbox)/([a-zA-Z0-9-]+)", content)
        if trans_match:
            order_id = trans_match.group(1)
        else:
            order_id = f"ORD-{int(dt.timestamp())}"

        # Extract Buyer
        buyer = ""
        buyer_match = re.search(r"sold to\s+@?([\w.-]+)", content, re.IGNORECASE)
        if buyer_match:
            buyer = buyer_match.group(1)

        # Extract Item Title from content if present
        title = ""
        title_match = re.search(r"(?:item|article):\s*([^\n\r<]+)", content, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

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
        )

    return ParsedEmailEvent(
        event_type="unknown",
        raw_subject=sub_clean,
        timestamp=dt,
    )

