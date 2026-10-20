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

