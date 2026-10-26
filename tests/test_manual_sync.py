"""Unit tests for manual listing manager: moving items between sheets and updating metadata."""

from unittest.mock import MagicMock
import pytest

from src.sheets.sync import SheetsSyncEngine
from src.sheets.schemas import (
    TAB_ITEM_LISTINGS,
    TAB_SOLD_ITEMS,
    TAB_HIDDEN_ITEMS,
    TAB_CANCELLED_ORDERS,
    TAB_INVENTORY_LOTS,
    TAB_SHIPPING_SUPPLIES,
    LISTINGS_HEADERS,
    SOLD_ITEMS_HEADERS,
    HIDDEN_ITEMS_HEADERS,
    CANCELLED_ORDERS_HEADERS,
    LOTS_HEADERS,
    SUPPLIES_HEADERS,
)
from src.sheets.reconcile import L, S, C
from tests.test_sheets_sync import MockSpreadsheet, MockWorksheet


@pytest.fixture
def manual_engine_and_sh():
    ws_lots = MockWorksheet(
        TAB_INVENTORY_LOTS,
        headers=LOTS_HEADERS,
        initial_rows=[
            ["LOT-001", "2026-10-15", "Goodwill", "100.00", "20", "5.00", "0", "Active", "Haul 1"],
        ],
    )
    ws_listings = MockWorksheet(
        TAB_ITEM_LISTINGS,
        headers=LISTINGS_HEADERS,
        initial_rows=[
            [
                "SKU-1001",
                "LOT-001",
                "Vintage Levi's 501 Jeans",
                "Levi's",
                "Bottoms",
                "=IFERROR(VLOOKUP(B2, Inventory_Lots!A:F, 6, FALSE), 0)",
                "50.00",
                "45.00",
                "YES",
                "5.00",
                "10/20: $50.00 -> $45.00",
                "12",
                "48",
                "123456789",
                "https://www.vinted.com/items/123456789-vintage-levis-501-jeans",
                "Listed",
                "2026-10-20",
                "2026-10-25 10:00:00",
                "HTML",
            ],
            [
                "SKU-1002",
                "LOT-001",
                "Nike Windbreaker Jacket",
                "Nike",
                "Outerwear",
                "=IFERROR(VLOOKUP(B3, Inventory_Lots!A:F, 6, FALSE), 0)",
                "40.00",
                "40.00",
                "NO",
                "0.00",
                "",
                "5",
                "22",
                "987654321",
                "https://www.vinted.com/items/987654321-nike-windbreaker-jacket",
                "Listed",
                "2026-10-21",
                "2026-10-25 10:00:00",
                "HTML",
            ],
        ],
    )
    ws_sold = MockWorksheet(TAB_SOLD_ITEMS, headers=SOLD_ITEMS_HEADERS, initial_rows=[])
    ws_hidden = MockWorksheet(TAB_HIDDEN_ITEMS, headers=HIDDEN_ITEMS_HEADERS, initial_rows=[])
    ws_cancelled = MockWorksheet(TAB_CANCELLED_ORDERS, headers=CANCELLED_ORDERS_HEADERS, initial_rows=[])
    ws_supplies = MockWorksheet(
        TAB_SHIPPING_SUPPLIES,
        headers=SUPPLIES_HEADERS,
        initial_rows=[["SUP-01", "2026-10-01", "Polymailers", "Amazon", "100", "24.00", "0.24", "0.24"]],
    )

    sh = MockSpreadsheet({
        TAB_INVENTORY_LOTS: ws_lots,
        TAB_ITEM_LISTINGS: ws_listings,
        TAB_SOLD_ITEMS: ws_sold,
        TAB_HIDDEN_ITEMS: ws_hidden,
        TAB_CANCELLED_ORDERS: ws_cancelled,
        TAB_SHIPPING_SUPPLIES: ws_supplies,
    })
    client = MagicMock()
    client.get_spreadsheet.return_value = sh
    return SheetsSyncEngine(client), sh


def test_manual_move_active_to_sold_external_platform(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    assert len(ws_listings.rows) == 3  # header + 2 items
    assert len(ws_sold.rows) == 1      # header only

    res = eng.manual_move_item(
        source_tab=TAB_ITEM_LISTINGS,
        dest_tab=TAB_SOLD_ITEMS,
        source_sheet_row=2,  # Vintage Levi's Jeans
        item_data={
            "sold_price": 42.00,
            "sale_date": "2026-10-25",
            "platform": "eBay",
            "order_id": "EBAY-9921",
            "status": "Sold",
        },
    )

    assert res["status"] == "success"
    # Source sheet row deleted: now only Nike Windbreaker remains
    assert len(ws_listings.rows) == 2
    assert ws_listings.rows[1][L["Title"]] == "Nike Windbreaker Jacket"

    # Destination sheet row appended
    assert len(ws_sold.rows) == 2
    sold_row = ws_sold.rows[1]
    assert sold_row[S["Title"]] == "Vintage Levi's 501 Jeans"
    assert sold_row[S["Platform"]] == "eBay"
    assert sold_row[S["Sold_Price"]] == 42.00
    assert sold_row[S["Sale_Date"]] == "2026-10-25"
    assert sold_row[S["Order_ID"]] == "EBAY-9921"
    assert sold_row[S["Source"]] == "Manual"

    # Verify formulas generated for row 2
    assert sold_row[S["Unit_Cost"]] == "=IFERROR(VLOOKUP(B2, Inventory_Lots!A:F, 6, FALSE), 0)"
    assert sold_row[S["Net_Profit"]] == "=IF(AND(ISNUMBER(L2), ISNUMBER(F2), ISNUMBER(Q2)), L2-F2-Q2, IF(ISNUMBER(L2), L2, 0))"
    assert sold_row[S["Margin_%"]] == "=IF(AND(ISNUMBER(L2), L2>0), R2/L2, 0)"


def test_manual_move_local_pickup_zero_packaging(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    eng.manual_move_item(
        source_tab=TAB_ITEM_LISTINGS,
        dest_tab=TAB_SOLD_ITEMS,
        source_sheet_row=2,
        item_data={
            "sold_price": 35.00,
            "sale_date": "2026-10-25",
            "platform": "FB Marketplace (Local)",
            "status": "Sold",
        },
    )

    sold_row = ws_sold.rows[1]
    assert sold_row[S["Platform"]] == "FB Marketplace (Local)"
    # The formula checks IF(P2="FB Marketplace (Local)", 0, ...)
    assert 'IF(P2="FB Marketplace (Local)", 0' in sold_row[S["Packaging_Cost"]]


def test_manual_move_active_to_hidden_and_back(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_hidden = sh.worksheet(TAB_HIDDEN_ITEMS)

    # 1. Move to hidden
    eng.manual_move_item(
        source_tab=TAB_ITEM_LISTINGS,
        dest_tab=TAB_HIDDEN_ITEMS,
        source_sheet_row=2,
    )
    assert len(ws_listings.rows) == 2
    assert len(ws_hidden.rows) == 2
    assert ws_hidden.rows[1][L["Title"]] == "Vintage Levi's 501 Jeans"
    assert ws_hidden.rows[1][L["Status"]] == "Hidden"

    # 2. Move back to active
    eng.manual_move_item(
        source_tab=TAB_HIDDEN_ITEMS,
        dest_tab=TAB_ITEM_LISTINGS,
        source_sheet_row=2,
        item_data={"status": "Listed"},
    )
    assert len(ws_hidden.rows) == 1
    assert len(ws_listings.rows) == 3
    assert ws_listings.rows[2][L["Title"]] == "Vintage Levi's 501 Jeans"
    assert ws_listings.rows[2][L["Status"]] == "Listed"


def test_manual_move_sold_to_cancelled(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_cancelled = sh.worksheet(TAB_CANCELLED_ORDERS)

    # Move active -> sold first
    eng.manual_move_item(
        source_tab=TAB_ITEM_LISTINGS,
        dest_tab=TAB_SOLD_ITEMS,
        source_sheet_row=2,
        item_data={"sold_price": 50.00, "platform": "Vinted"},
    )
    assert len(ws_sold.rows) == 2

    # Now move sold -> cancelled
    eng.manual_move_item(
        source_tab=TAB_SOLD_ITEMS,
        dest_tab=TAB_CANCELLED_ORDERS,
        source_sheet_row=2,
        item_data={"notes": "Buyer changed mind, refund issued"},
    )
    assert len(ws_sold.rows) == 1
    assert len(ws_cancelled.rows) == 2
    c_row = ws_cancelled.rows[1]
    assert c_row[C["Item_Title"]] == "Vintage Levi's 501 Jeans"
    assert c_row[C["Lost_Revenue"]] == 50.00
    assert c_row[C["Status_Details"]] == "Buyer changed mind, refund issued"


def test_manual_in_place_update(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    eng.manual_update_row(
        tab_name=TAB_ITEM_LISTINGS,
        sheet_row=2,
        updated_fields={
            "Title": "Custom Updated Title",
            "Current_Price": 39.99,
            "Lot_ID": "LOT-999",
        },
    )

    row = ws_listings.rows[1]
    assert row[L["Title"]] == "Custom Updated Title"
    assert row[L["Current_Price"]] == 39.99
    assert row[L["Lot_ID"]] == "LOT-999"
