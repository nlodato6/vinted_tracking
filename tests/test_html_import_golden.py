"""Golden tests: real (trimmed) Vinted exports -> plan -> apply on an in-memory sheet.

Expected results for the fixtures (exported 2026-10-25):
- 170 items on the active closet, 181 on the sold closet. 'Blue V Neck' (same Vinted ID) is on both
  because it sold between the two exports, so it counts as sold -> 169 active.
- 5 cancelled orders. 4 of them link to an item shown on the sold closet (Joe's Jeans,
  Navy Blue Button down collar, one DSG Sports Bra, 21 Pack of Boxer Briefs); the 5th is a bundle.
  Linked items are moved to Cancelled_Orders, so Sold_Items = 181 - 4 = 177.
- The '_all' export also shows 11 hidden items.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.html_parser import build_snapshot
from src.sheets.reconcile import S, build_sync_plan
from src.sheets.schemas import (
    CANCELLED_ORDERS_HEADERS,
    HIDDEN_ITEMS_HEADERS,
    LISTINGS_HEADERS,
    LOTS_HEADERS,
    SALES_HEADERS,
    SOLD_ITEMS_HEADERS,
    SUPPLIES_HEADERS,
    TAB_CANCELLED_ORDERS,
    TAB_HIDDEN_ITEMS,
    TAB_INVENTORY_LOTS,
    TAB_ITEM_LISTINGS,
    TAB_SALES_LOG,
    TAB_SHIPPING_SUPPLIES,
    TAB_SOLD_ITEMS,
)
from src.sheets.sync import SheetsSyncEngine
from tests.test_sheets_sync import MockSpreadsheet, MockWorksheet

FX = Path(__file__).parent / "fixtures"
ACTIVE = ("tlodato24 - member profile _ Vinted.html", FX / "closet_active.html")
SOLD = ("tlodato24 - member profile _ Vinted_sold.html", FX / "closet_sold.html")
ALL = ("tlodato24 - member profile _ Vinted_all.html", FX / "closet_all.html")
ORDERS = ("My orders _ Vinted.html", FX / "my_orders.html")


def _engine():
    sh = MockSpreadsheet({
        TAB_INVENTORY_LOTS: MockWorksheet(TAB_INVENTORY_LOTS, LOTS_HEADERS, [["LOT-001", "2026-10-01", "x", "199", "351", "", "", "", ""]]),
        TAB_ITEM_LISTINGS: MockWorksheet(TAB_ITEM_LISTINGS, LISTINGS_HEADERS),
        TAB_SOLD_ITEMS: MockWorksheet(TAB_SOLD_ITEMS, SOLD_ITEMS_HEADERS),
        TAB_HIDDEN_ITEMS: MockWorksheet(TAB_HIDDEN_ITEMS, HIDDEN_ITEMS_HEADERS),
        TAB_CANCELLED_ORDERS: MockWorksheet(TAB_CANCELLED_ORDERS, CANCELLED_ORDERS_HEADERS),
        TAB_SALES_LOG: MockWorksheet(TAB_SALES_LOG, SALES_HEADERS),
        TAB_SHIPPING_SUPPLIES: MockWorksheet(TAB_SHIPPING_SUPPLIES, SUPPLIES_HEADERS, [["SUP-01", "", "", "", "", "", "", "0.24"]]),
    })
    client = MagicMock()
    client.get_spreadsheet.return_value = sh
    return SheetsSyncEngine(client), sh


def _upload(engine, *files, lot="LOT-001"):
    snap = build_snapshot([(name, path.read_bytes()) for name, path in files], default_lot=lot)
    state = engine.read_sheet_state()
    plan = build_sync_plan(snap, state, default_lot=lot)
    engine.apply_sync_plan(plan, state)
    return plan


def _counts(sh):
    n = lambda t: sum(1 for r in sh.worksheet(t).rows[1:] if any(str(c).strip() for c in r))
    return {"active": n(TAB_ITEM_LISTINGS), "sold": n(TAB_SOLD_ITEMS), "hidden": n(TAB_HIDDEN_ITEMS), "cancelled": n(TAB_CANCELLED_ORDERS)}


TARGET = {"active": 169, "sold": 177, "hidden": 0, "cancelled": 5}


def test_all_three_files_in_one_upload():
    eng, sh = _engine()
    plan = _upload(eng, ACTIVE, SOLD, ORDERS)
    assert _counts(sh) == TARGET
    assert plan.counts_after == TARGET  # preview numbers equal the written result


@pytest.mark.parametrize("order", [
    [[ACTIVE], [SOLD], [ORDERS]],
    [[SOLD], [ORDERS], [ACTIVE]],
    [[SOLD, ORDERS], [ACTIVE]],
    [[ACTIVE, ORDERS], [SOLD, ORDERS]],
])
def test_separate_uploads_any_order(order):
    eng, sh = _engine()
    for files in order:
        _upload(eng, *files)
    assert _counts(sh) == TARGET


def test_orders_before_sold_links_later_and_warns_when_ambiguous():
    eng, sh = _engine()
    _upload(eng, ORDERS)
    plan = _upload(eng, SOLD)
    # Joe's Jeans, Navy Blue Button down collar and 21 Pack are unique titles -> linked now.
    # DSG Sports Bra has 3 sold items; without the orders file we can't tell which one -> warning.
    assert _counts(sh)["sold"] == 178
    assert any("DSG Sports Bra" in w for w in plan.warnings)
    # Re-uploading My Orders alongside resolves it.
    _upload(eng, ORDERS, ACTIVE)
    assert _counts(sh) == TARGET


def test_resync_is_idempotent():
    eng, sh = _engine()
    _upload(eng, ACTIVE, SOLD, ORDERS)
    snapshot_rows = {t: [list(r) for r in sh.worksheet(t).rows] for t in (TAB_SOLD_ITEMS, TAB_CANCELLED_ORDERS, TAB_HIDDEN_ITEMS)}
    plan = _upload(eng, ACTIVE, SOLD, ORDERS)
    assert _counts(sh) == TARGET
    assert not plan.sold_appends and not plan.cancelled_appends and not plan.hidden_appends and not plan.sold_delete_rows
    for t, rows in snapshot_rows.items():
        assert sh.worksheet(t).rows == rows


def test_all_export_routes_hidden_and_sold():
    eng, sh = _engine()
    _upload(eng, ALL, ORDERS)
    # _all has 170 listed / 180 sold / 11 hidden; Blue V Neck is still 'listed' there.
    assert _counts(sh) == {"active": 170, "sold": 176, "hidden": 11, "cancelled": 5}
    # Adding the newer sold export moves Blue V Neck from active to sold.
    _upload(eng, SOLD)
    assert _counts(sh) == {"active": 169, "sold": 177, "hidden": 11, "cancelled": 5}


def test_shared_titles_do_not_move_live_listings():
    """'black tee', 'sleeveless blouse', etc. exist both live and sold; matching is by ID only."""
    eng, sh = _engine()
    _upload(eng, ACTIVE)
    _upload(eng, SOLD)
    titles = [r[2].lower() for r in sh.worksheet(TAB_ITEM_LISTINGS).rows[1:]]
    assert titles.count("sleeveless blouse") == 3
    assert "black tee" in titles and "white polo shirt" in titles
    assert "blue v neck" not in titles


def test_cancelled_items_linked_and_excluded_from_sold():
    eng, sh = _engine()
    _upload(eng, SOLD, ORDERS)
    canc = sh.worksheet(TAB_CANCELLED_ORDERS).rows[1:]
    linked = [r for r in canc if "/items/" in r[10]]
    assert len(linked) == 4
    assert any(r[2].lower().startswith("bundle") and "/inbox/" in r[10] for r in canc)
    sold_titles = [r[S["Title"]] for r in sh.worksheet(TAB_SOLD_ITEMS).rows[1:]]
    assert "21 Pack of Boxer Briefs" not in sold_titles
    assert sold_titles.count("DSG Sports Bra") == 2


def test_new_sold_rows_have_blank_sale_date_and_formulas():
    eng, sh = _engine()
    _upload(eng, SOLD)
    rows = sh.worksheet(TAB_SOLD_ITEMS).rows
    for n, r in enumerate(rows[1:], start=2):
        assert r[S["Sale_Date"]] == ""
        assert r[S["Unit_Cost"]] == f"=IFERROR(VLOOKUP(B{n}, Inventory_Lots!A:F, 6, FALSE), 0)"
        assert r[S["Net_Profit"]].startswith(f"=IF(AND(ISNUMBER(L{n})")
        assert r[S["Lot_ID"]] == "LOT-001"


def test_orders_only_upload_records_cancellations_without_sold_rows():
    eng, sh = _engine()
    plan = _upload(eng, ORDERS)
    assert _counts(sh) == {"active": 0, "sold": 0, "hidden": 0, "cancelled": 5}
    assert any("Upload your Sold closet" in w for w in plan.warnings)


def test_listing_overrides_apply_to_new_listings():
    eng, sh = _engine()
    snap = build_snapshot([(ACTIVE[0], ACTIVE[1].read_bytes())], default_lot="LOT-001")
    vid = next(iter(snap.items))
    plan = build_sync_plan(snap, eng.read_sheet_state(), default_lot="LOT-001", listing_overrides={vid: {"title": "Edited Title", "lot": ""}})
    row = next(r for r in plan.final_listings if r[13] == vid)
    assert row[2] == "Edited Title" and row[1] == ""


def test_dirty_sheet_reconciliation_cleans_duplicates_and_recovers_active_items():
    """If past buggy imports put active items in Sold_Items and added duplicate sold rows,
    re-uploading the HTML files recovers the active items and prunes the duplicates."""
    eng, sh = _engine()
    # 1. Clean import
    _upload(eng, ACTIVE, SOLD, ORDERS)

    # 2. Artificially dirty the sheet: 10 active listings moved to sold, plus duplicate sold rows
    listings = sh.worksheet(TAB_ITEM_LISTINGS).rows[1:]
    sold_rows = sh.worksheet(TAB_SOLD_ITEMS).rows[1:]
    hidden_rows = sh.worksheet(TAB_HIDDEN_ITEMS).rows[1:]
    canc_rows = sh.worksheet(TAB_CANCELLED_ORDERS).rows[1:]

    sh.worksheet(TAB_ITEM_LISTINGS).rows = [LISTINGS_HEADERS] + listings[10:]
    sh.worksheet(TAB_SOLD_ITEMS).rows = [SOLD_ITEMS_HEADERS] + sold_rows + listings[:10] + sold_rows[:14]

    dirty_counts = _counts(sh)
    assert dirty_counts["active"] == 159
    assert dirty_counts["sold"] == 201

    # 3. Reconcile with the uploaded HTML files
    plan = _upload(eng, ACTIVE, SOLD, ORDERS)
    assert _counts(sh) == TARGET
    assert plan.counts_after == TARGET

