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


def test_manual_update_cancelled_in_place_preserves_row_and_sets_zero_loss(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_cancelled = sh.worksheet(TAB_CANCELLED_ORDERS)

    # Seed a cancelled order row
    ws_cancelled.rows.append([
        "CAN-1001",
        "SKU-2001",
        "Bundle 3 items",
        "Nike",
        "Bundle",
        "45.00",
        "45.00",
        "2026-10-20",
        "Order cancelled by buyer",
        "buyer_alice",
        "https://www.vinted.com/items/bundle-1",
        "Email",
    ])

    # In-place update: adjust to $0.00 loss and add explanation note
    eng.manual_update_row(
        tab_name=TAB_CANCELLED_ORDERS,
        sheet_row=2,
        updated_fields={
            "Lost_Revenue": 0.0,
            "Status_Details": "Customer re-ordered larger bundle, 0 loss",
            "Order_ID": "CAN-BUN-NEW",
        },
    )

    # Verify row was updated in-place and not duplicated or removed
    assert len(ws_cancelled.rows) == 2
    row = ws_cancelled.rows[1]
    assert row[C["Order_ID"]] == "CAN-BUN-NEW"
    assert row[C["Item_Title"]] == "Bundle 3 items"
    assert row[C["Lost_Revenue"]] == 0.0
    assert row[C["Status_Details"]] == "Customer re-ordered larger bundle, 0 loss"


def test_relist_cancelled_to_active_listings_preserves_cancel_record(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_cancelled = sh.worksheet(TAB_CANCELLED_ORDERS)
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # Seed a cancelled order row
    ws_cancelled.rows.append([
        "CAN-1002",
        "SKU-3001",
        "Carhartt Jacket",
        "Carhartt",
        "Outerwear",
        "60.00",
        "60.00",
        "2026-10-21",
        "Buyer changed mind",
        "buyer_bob",
        "https://www.vinted.com/items/555-jacket",
        "Email",
    ])

    initial_listings_count = len(ws_listings.rows)

    # Relist into Active Listings with delete_source=False
    eng.manual_move_item(
        source_tab=TAB_CANCELLED_ORDERS,
        dest_tab=TAB_ITEM_LISTINGS,
        source_sheet_row=2,
        item_data={
            "title": "Carhartt Jacket",
            "lot": "LOT-001",
            "current_price": 55.00,
            "posted_price": 55.00,
            "status": "Listed",
            "vinted_url": "https://www.vinted.com/items/555-jacket",
        },
        delete_source=False,
    )

    # Cancellation record must be preserved for audit & email idempotency
    assert len(ws_cancelled.rows) == 2
    canc_row = ws_cancelled.rows[1]
    assert "Relisted to Active Listings" in canc_row[C["Status_Details"]]
    assert canc_row[C["Order_ID"]] == "CAN-1002"

    # Active listings must have the new relisted row
    assert len(ws_listings.rows) == initial_listings_count + 1
    new_listing = ws_listings.rows[-1]
    assert new_listing[L["Title"]] == "Carhartt Jacket"
    assert new_listing[L["Item_SKU"]] == "SKU-3001"
    assert new_listing[L["Lot_ID"]] == "LOT-001"
    assert new_listing[L["Current_Price"]] == 55.00
    assert new_listing[L["Status"]] == "Listed"
    assert new_listing[L["Vinted_ID"]] == "555"


def test_manual_delete_row(manual_engine_and_sh):
    eng, sh = manual_engine_and_sh
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Initial count in Item_Listings is 3 rows (header + 2 data rows)
    assert len(ws_listings.rows) == 3
    first_item_sku = ws_listings.rows[1][L["Item_SKU"]]
    second_item_sku = ws_listings.rows[2][L["Item_SKU"]]

    # 1. Header protection: deleting row 1 must raise ValueError
    with pytest.raises(ValueError, match="Invalid row 1"):
        eng.manual_delete_row(TAB_ITEM_LISTINGS, sheet_row=1)

    # 2. Out-of-bounds protection: deleting row > len(rows) must raise ValueError
    with pytest.raises(ValueError, match="Invalid row 999"):
        eng.manual_delete_row(TAB_ITEM_LISTINGS, sheet_row=999)

    # 3. Successful deletion of row 2
    res = eng.manual_delete_row(TAB_ITEM_LISTINGS, sheet_row=2)
    assert res is True
    assert len(ws_listings.rows) == 2
    # The remaining data row is what used to be row 3
    assert ws_listings.rows[1][L["Item_SKU"]] == second_item_sku

    # 4. Deletion from Sold_Items triggers sync_lots
    # Add a data row to Sold_Items
    ws_sold.rows.append(["CAN-SOLD-1", "SKU-SOLD", "LOT-001", "Sold Item", "Brand", "Cat", "5.00", "25.00", "Vinted"])
    assert len(ws_sold.rows) == 2
    eng.sync_lots = MagicMock()
    eng.manual_delete_row(TAB_SOLD_ITEMS, sheet_row=2)
    assert len(ws_sold.rows) == 1
    eng.sync_lots.assert_called_once()



