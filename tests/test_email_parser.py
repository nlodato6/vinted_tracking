"""Unit tests for Vinted transactional email parser."""

from datetime import datetime
from src.email_parser import parse_vinted_email


def test_parse_favorited_email():
    subject = "Face Print Pants was just favorited"
    body = """
    Hi seller,
    @shopper99 added your item to favorites!
    View your item: https://www.vinted.com/items/10437985710-face-print-pants
    Send an offer to buyers interested in your item.
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "favorite"
    assert event.item_title == "Face Print Pants"
    assert event.vinted_id == "10437985710"
    assert "https://www.vinted.com/items/10437985710" in event.vinted_url
    assert event.buyer_username == "shopper99"


def test_parse_sold_email():
    subject = "You sold an item on Vinted!"
    body = """
    Congratulations on your sale!
    Item: Alabama Sweatpants
    Sold to @vintagefan
    Total earnings: $2.00
    View order details: https://www.vinted.com/transaction/01a1569f-be78-78a1-a263-1fdb328d3780
    Item link: https://www.vinted.com/items/10438001352-alabama-sweatpants
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "sale"
    assert event.item_title == "Alabama Sweatpants"
    assert event.sold_price == 2.00
    assert event.vinted_id == "10438001352"
    assert event.order_id == "01a1569f-be78-78a1-a263-1fdb328d3780"
    assert event.buyer_username == "vintagefan"


def test_parse_offer_email():
    subject = "New offer for Face Print Pants"
    body = """
    @bargainhunter sent you an offer!
    $8.00 instead of $10.00
    View the offer: https://www.vinted.com/items/10437985710-face-print-pants
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "offer"
    assert event.item_title == "Face Print Pants"
    assert event.offer_price == 8.00
    assert event.listed_price == 10.00
    assert event.vinted_id == "10437985710"
    assert event.buyer_username == "bargainhunter"


def test_parse_unknown_email():
    subject = "Weekly summary of tips"
    body = "Here are tips to improve your wardrobe."
    event = parse_vinted_email(subject, body)
    assert event.event_type == "unknown"


def test_decode_mime_words():
    from src.email_parser import decode_mime_words
    assert decode_mime_words("Plain Subject") == "Plain Subject"
    assert decode_mime_words(None) == ""


def test_fetch_vinted_emails_empty_credentials():
    from src.email_parser import fetch_vinted_emails_via_imap
    assert fetch_vinted_emails_via_imap("", "") == []


def test_parse_sold_email_slug_fallback():
    # Email with no explicit 'Item:' text, only URL slug
    subject = "You sold an item on Vinted!"
    body = """
    Congratulations on your sale!
    Sold to @shopper1
    Earnings: $45.00
    Transaction link: https://www.vinted.com/transaction/01a1569f-be78
    Item: https://www.vinted.com/items/987654321-vintage-carhartt-hoodie
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "sale"
    assert event.item_title == "Vintage Carhartt Hoodie"
    assert event.brand == "Carhartt"
    assert event.category == "Sweaters & Hoodies"
    assert event.sold_price == 45.00


def test_parse_sold_email_html_anchor():
    # Email with HTML anchor link
    subject = "You sold an item on Vinted!"
    body = """
    <div>
        <p>Congratulations, your item was bought by @collector!</p>
        <a href="https://www.vinted.com/items/12345678">Nike Air Max Sneakers</a>
        <span>Total: $85.00</span>
    </div>
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "sale"
    assert event.item_title == "Nike Air Max Sneakers"
    assert event.brand == "Nike"
    assert event.category == "Footwear"
    assert event.sold_price == 85.00


def test_parse_sold_email_img_alt_cdn():
    subject = "You sold an item on Vinted!"
    body = """
    <html>
      <body>
        <h1>Congratulations on your sale!</h1>
        <p>You sold an item to @fashionfan!</p>
        <img src="https://images1.vinted.net/t/04_01c45_abcdef12345/f800/12345.jpeg" alt="Carhartt WIP Michigan Chore Coat" width="100">
        <a href="https://www.vinted.com/transaction/01a1569f-be78-78a1-a263-1fdb328d3780">View order details</a>
        <div>Total earnings: $68.00</div>
      </body>
    </html>
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "sale"
    assert event.item_title == "Carhartt WIP Michigan Chore Coat"
    assert event.brand == "Carhartt"
    assert event.category == "Outerwear"
    assert event.sold_price == 68.00
    assert event.order_id == "01a1569f-be78-78a1-a263-1fdb328d3780"


def test_parse_sold_email_html_unescape_and_explicit_brand():
    subject = "You sold an item on Vinted!"
    body = """
    <div>
      <p>Congratulations, your item was sold!</p>
      <a href="https://www.vinted.com/transaction/01a2b3c4-9999">Levi&#39;s &amp; Vintage Denim Trucker Jacket</a>
      <p>Brand: Levi&#39;s</p>
      <p>Category: Outerwear</p>
      <p>Price: $42.50</p>
    </div>
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "sale"
    assert "Levi's & Vintage Denim Trucker Jacket" in event.item_title
    assert "Levi" in event.brand
    assert event.category == "Outerwear"
    assert event.sold_price == 42.50
    assert event.order_id == "01a2b3c4-9999"


def test_fetch_vinted_emails_scans_processed_label(monkeypatch):
    import imaplib
    from unittest.mock import MagicMock
    from src.email_parser import fetch_vinted_emails_via_imap

    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"1"])
    mock_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "[Gmail]/All Mail"'])

    # Simulate search returning message ID 101 for label:Vinted-Processed
    def mock_search(charset, *criteria):
        query_str = " ".join(str(c) for c in criteria)
        if "label:Vinted-Processed" in query_str:
            return ("OK", [b"101"])
        return ("OK", [b""])

    mock_mail.search.side_effect = mock_search

    # Simulate message fetch
    raw_email = (
        b"From: no-reply@vinted.com\r\n"
        b"Subject: You sold an item on Vinted!\r\n"
        b"Date: Wed, 22 Oct 2026 12:00:00 -0400\r\n"
        b"Content-Type: text/html\r\n\r\n"
        b"<html><body><img src='https://images1.vinted.net/item.jpg' alt='Patagonia Snap-T Fleece Pullover'>"
        b"<a href='https://www.vinted.com/transaction/test-uuid-1234'>Order details</a>"
        b"Earnings: $55.00</body></html>"
    )
    mock_mail.fetch.return_value = ("OK", [(b"101 (RFC822 {123})", raw_email)])

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: mock_mail)

    events = fetch_vinted_emails_via_imap("user@gmail.com", "app_password_1234", max_emails=10)
    assert len(events) == 1
    assert events[0].item_title == "Patagonia Snap-T Fleece Pullover"
    assert events[0].brand == "Patagonia"
    assert events[0].sold_price == 55.00
    assert events[0].order_id == "test-uuid-1234"


def test_parse_cancellation_email():
    subject = "Your order was cancelled"
    body = """
    <div>
        <h1>Order Cancelled</h1>
        <p>Your order for Joe's Jeans has been cancelled.</p>
        <p>Reason: Buyer cancelled the order.</p>
        <div>Refund of $3.40 processed.</div>
        <a href="https://www.vinted.com/transaction/01a156e8-test-cancel">View transaction</a>
        <p>Buyer: @denimfan</p>
    </div>
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "cancellation"
    assert "Joe's Jeans" in event.item_title
    assert event.sold_price == 3.40
    assert event.order_id == "01a156e8-test-cancel"
    assert "Buyer cancelled" in event.cancellation_reason
    assert event.buyer_username == "denimfan"


def test_parse_bundle_sale_email():
    subject = "You sold a bundle on Vinted!"
    body = """
    <div>
        <p>Congratulations! You sold a Bundle 5 items to @buyer99.</p>
        <span>Earnings: $15.00</span>
        <a href="https://www.vinted.com/transaction/01a156e8-bundle-5">Order details</a>
    </div>
    """
    event = parse_vinted_email(subject, body)
    assert event.event_type == "bundle_sale"
    assert event.quantity == 5
    assert event.sold_price == 15.00
    assert event.order_id == "01a156e8-bundle-5"


def test_resolve_imap_folder_quotes_spaces():
    from unittest.mock import MagicMock
    from src.email_parser import _resolve_imap_folder

    mock_mail = MagicMock()
    mock_mail.list.return_value = ("OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'])
    mock_mail.select.return_value = ("OK", [b"100"])

    resolved = _resolve_imap_folder(mock_mail, "ALL")
    assert resolved == '"[Gmail]/All Mail"'
    # Ensure mailbox names with spaces are properly quoted for IMAP compatibility
    mock_mail.select.assert_called_with('"[Gmail]/All Mail"', readonly=True)


def test_get_last_scan_date(monkeypatch):
    from datetime import date
    from src.email_parser import get_last_scan_date

    monkeypatch.setattr("src.notifications.get_sync_status", lambda: {"last_email_parsed_at": "2026-10-21 14:30"})
    d = get_last_scan_date()
    assert d == date(2026, 10, 21)

    monkeypatch.setattr("src.notifications.get_sync_status", lambda: {"last_email_parsed_at": "Not yet run"})
    assert get_last_scan_date() is None


def test_fetch_vinted_emails_date_range_query(monkeypatch):
    import imaplib
    from datetime import date
    from unittest.mock import MagicMock
    from src.email_parser import fetch_vinted_emails_via_imap

    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"1"])
    mock_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "[Gmail]/All Mail"'])

    searched_queries = []

    def mock_search(charset, *criteria):
        searched_queries.append(" ".join(str(c) for c in criteria))
        return ("OK", [b""])

    mock_mail.search.side_effect = mock_search
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: mock_mail)

    fetch_vinted_emails_via_imap(
        username="user@gmail.com",
        password="app_password",
        since_date=date(2026, 10, 1),
        until_date=date(2026, 10, 15),
    )

    # Check that after: and before: were passed to X-GM-RAW
    any_date_search = any("after:2026/10/01" in q and "before:2026/10/16" in q for q in searched_queries)
    assert any_date_search, f"Searched queries did not include expected date filter: {searched_queries}"




