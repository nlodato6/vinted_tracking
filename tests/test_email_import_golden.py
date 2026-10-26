"""Golden tests for email sync: real (anonymized, trimmed) Vinted emails -> plan -> apply on an in-memory sheet.

Fixture: tests/fixtures/emails/vinted_emails.jsonl (built by scripts/trim_vinted_emails.py), covering the
same period as the HTML fixtures plus one later day (2026-10-25):
- 155 shipping-label emails = 155 orders / 181 items; 12 orders are bundles (38 items).
- 6 cancellation emails: the 5 cancelled orders from My Orders (one is the 5-tee bundle) + Dodge Zipper Hoodie
  (cancelled on 10/25, after the HTML export).
- 3 'completed' + 3 'You sold' emails carry prices; 19 offers and 3 favorites carry Vinted item IDs.

After HTML import (169 active / 177 sold / 5 cancelled) the email sync must give:
- Sold 171: the 5 tees of the cancelled bundle and Dodge Zipper Hoodie move to Cancelled_Orders
  (181 label items - 10 cancelled items = 171).
- Every sold row has a Sale_Date and Order_ID (Transaction ID); bundle items share their Order_ID.
- Cancelled_Orders 6, all with a Cancellation_Date.
"""

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

from src.email_parser import (
    EmailAuthError,
    ParsedEmailEvent,
    _to_local_naive,
    fetch_vinted_emails_via_imap,
    parse_vinted_email,
    unwrap_vinted_link,
)
from src.sheets.reconcile import C, S, build_email_plan, split_bundle_total
from tests.test_html_import_golden import ACTIVE, ORDERS, SOLD, _counts, _engine, _upload

FX = Path(__file__).parent / "fixtures" / "emails" / "vinted_emails.jsonl"
WENDY_TXN = "23515429160"


def _events():
    out = []
    for line in FX.read_text().splitlines():
        r = json.loads(line)
        out.append(parse_vinted_email(r["subject"], r["html"], _to_local_naive(parsedate_to_datetime(r["date"])), r["message_id"]))
    return out


EVENTS = _events()


def _by_type(t):
    return [e for e in EVENTS if e.event_type == t]


def _email_sync(eng, events=None):
    state = eng.read_sheet_state()
    plan = build_email_plan(events if events is not None else EVENTS, state)
    eng.apply_sync_plan(plan, state)
    return plan


@pytest.fixture()
def synced():
    eng, sh = _engine()
    _upload(eng, ACTIVE, SOLD, ORDERS)
    plan = _email_sync(eng)
    return eng, sh, plan


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def test_parser_classifies_every_real_template():
    labels = _by_type("sale") + _by_type("bundle_sale")
    assert len(labels) == 155
    assert len({e.transaction_id for e in labels}) == 155
    assert all(e.transaction_id.isdigit() and len(e.transaction_id) == 11 for e in labels)
    assert len(_by_type("bundle_sale")) == 12
    assert sum(len(e.items) for e in labels) == 181
    assert len(_by_type("cancellation")) == 6
    assert len(_by_type("completed")) == 3 and all(e.sold_price and e.transaction_id for e in _by_type("completed"))
    assert len(_by_type("sale_notice")) == 3 and all(e.sold_price and e.item_title for e in _by_type("sale_notice"))
    offers = _by_type("offer")
    assert len(offers) == 19 and all(e.vinted_id and e.offer_price and e.listed_price for e in offers)
    assert len(_by_type("favorite")) == 3 and all(e.vinted_id for e in _by_type("favorite"))
    assert not _by_type("unknown")


def test_bundle_label_lists_every_item():
    wendy = next(e for e in EVENTS if e.transaction_id == WENDY_TXN)
    assert wendy.event_type == "bundle_sale"
    assert wendy.items == ["Wendy HeyDude", "Wendy HeyDude - Turquoise"]
    assert wendy.quantity == 2


def test_cancellation_carries_vinted_conversation_id():
    dsg = next(e for e in _by_type("cancellation") if e.item_title == "DSG Sports Bra")
    assert dsg.order_id == "01a12950-c8c3-75e4-9dc9-a6ae6085b084"  # same ID the My Orders HTML import stores


def test_european_price_format_in_offer():
    ev = next(e for e in _by_type("offer") if e.item_title == "Low Rise Baggy Shorts")
    assert (ev.offer_price, ev.listed_price) == (3.0, 5.0)


def test_unwrap_vinted_tracking_link():
    href = "https://links.vinted.com/t/aHR0cHM6Ly93d3cudmludGVkLmNvbS9lL2l0ZW0_aWQ9MTAyMDk3MTY5NTV8dG9rZW4"
    assert unwrap_vinted_link(href) == "https://www.vinted.com/e/item?id=10209716955"


def test_email_dates_are_converted_to_local_time():
    utc = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
    assert _to_local_naive(utc) == utc.astimezone().replace(tzinfo=None)
    assert _to_local_naive(utc).tzinfo is None


def test_imap_login_failure_is_reported(monkeypatch):
    import imaplib

    class Boom:
        def __init__(self, host):
            pass

        def login(self, u, p):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Boom)
    assert fetch_vinted_emails_via_imap("a@gmail.com", "bad pass") == []
    with pytest.raises(EmailAuthError):
        fetch_vinted_emails_via_imap("a@gmail.com", "bad pass", raise_on_auth_error=True)


# ---------------------------------------------------------------------------
# Planner / sheet results
# ---------------------------------------------------------------------------
def test_email_sync_after_html_import(synced):
    eng, sh, plan = synced
    assert _counts(sh) == {"active": 169, "sold": 171, "hidden": 0, "cancelled": 6}
    assert plan.counts_after == _counts(sh)  # preview equals result
    assert not plan.email_unmatched
    sold = sh.worksheet("Sold_Items").rows[1:]
    assert all(str(r[S["Sale_Date"]]).strip() for r in sold)
    assert all(str(r[S["Order_ID"]]).strip() for r in sold)


def test_bundles_share_order_id_and_use_bundle_formulas(synced):
    eng, sh, plan = synced
    rows = sh.worksheet("Sold_Items").rows
    wendy = [(n, r) for n, r in enumerate(rows[1:], start=2) if str(r[S["Order_ID"]]) == WENDY_TXN]
    assert sorted(r[S["Title"]] for _, r in wendy) == ["Wendy HeyDude", "Wendy HeyDude - Turquoise"]
    for n, r in wendy:
        assert "COUNTIF(V:V" in str(r[S["Sale_Type"]]) and '"Bundle"' in str(r[S["Sale_Type"]])
        assert "COUNTIF(V$1:V" in str(r[S["Packaging_Cost"]])
    assert len(plan.email_bundles) == 11  # 12 bundles, one of them cancelled


def test_cancellations_are_dated_and_moved(synced):
    eng, sh, plan = synced
    canc = sh.worksheet("Cancelled_Orders").rows[1:]
    assert all(str(r[C["Cancellation_Date"]]).strip() for r in canc)
    bundle = next(r for r in canc if str(r[C["Item_Title"]]).startswith("Bundle 5 items"))
    assert bundle[C["Cancellation_Date"]] == "2026-10-13"
    assert len(str(bundle[C["Vinted_URL"]]).split("\n")) == 5  # all 5 tees linked
    dodge = next(r for r in canc if r[C["Item_Title"]] == "Dodge Zipper Hoodie")
    assert dodge[C["Source"]] == "Email" and "/items/" in dodge[C["Vinted_URL"]]
    sold_titles = [r[S["Title"]] for r in sh.worksheet("Sold_Items").rows[1:]]
    assert "Dodge Zipper Hoodie" not in sold_titles
    assert sold_titles.count("Blue tee") == 1  # the re-sold tee from the 6-tee bundle stays


def test_email_sync_is_idempotent(synced):
    eng, sh, _ = synced
    plan2 = build_email_plan(EVENTS, eng.read_sheet_state())
    assert not plan2.has_changes
    assert plan2.counts_after == _counts(sh)


def test_html_reimport_after_email_sync_is_stable(synced):
    eng, sh, _ = synced
    _upload(eng, ACTIVE, SOLD, ORDERS)
    assert _counts(sh) == {"active": 169, "sold": 171, "hidden": 0, "cancelled": 6}


def test_offers_logged_once(synced):
    eng, sh, plan = synced
    assert {o["Title"] for o in plan.email_offers} == {"Paisley Top", "Skinny Jeans"}
    hist = [r[11 - 1] for r in sh.worksheet("Item_Listings").rows[1:] if r[2] == "Paisley Top"][0]
    assert hist.count("Offer: $2.10") == 1


def test_email_without_html_import_creates_nothing():
    eng, sh = _engine()
    plan = build_email_plan(EVENTS, eng.read_sheet_state())
    assert not plan.sold_appends and not plan.sold_cell_updates
    assert len(plan.email_unmatched) == 181 - 10  # every non-cancelled item, reported instead of guessed


# ---------------------------------------------------------------------------
# Bundle price split
# ---------------------------------------------------------------------------
def test_split_bundle_total():
    assert split_bundle_total(12.0, [10, 5, 5]) == [6.0, 3.0, 3.0]
    assert sum(split_bundle_total(10.0, [3, 3, 3])) == pytest.approx(10.0)
    assert split_bundle_total(9.0, [4, 0, 2]) == [3.0, 3.0, 3.0]  # unknown listed price -> even split


def test_completed_email_price_is_split_across_bundle_items():
    eng, sh = _engine()
    _upload(eng, ACTIVE, SOLD, ORDERS)
    completed = ParsedEmailEvent(
        event_type="completed", transaction_id=WENDY_TXN, order_id=WENDY_TXN, sold_price=10.0,
        timestamp=datetime(2026, 10, 30, 12, 0), message_id="<test-completed-wendy>",
    )
    _email_sync(eng, EVENTS + [completed])
    rows = [r for r in sh.worksheet("Sold_Items").rows[1:] if str(r[S["Order_ID"]]) == WENDY_TXN]
    prices = [float(r[S["Sold_Price"]]) for r in rows]
    assert sum(prices) == pytest.approx(10.0)
    assert len(prices) == 2
