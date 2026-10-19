"""Unit tests for SheetsSyncEngine, Sold_Items separation, and date lot assignment."""

import re
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest

from src.sheets.sync import SheetsSyncEngine, normalize_date_str
from src.sheets.schemas import (
    TAB_ITEM_LISTINGS,
    TAB_SOLD_ITEMS,
    TAB_SALES_LOG,
    TAB_INVENTORY_LOTS,
    LISTINGS_HEADERS,
    SOLD_ITEMS_HEADERS,
    SALES_HEADERS,
    LOTS_HEADERS,
)
from src.models import ItemListing, SalesOrder


class MockWorksheet:
    def __init__(self, title, headers=None, initial_rows=None):
        self.title = title
        self.rows = []
        if headers:
            self.rows.append(list(headers))
        if initial_rows:
            for r in initial_rows:
                self.rows.append(list(r))

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def get_all_records(self):
        if len(self.rows) <= 1:
            return []
        headers = self.rows[0]
        return [dict(zip(headers, r)) for r in self.rows[1:]]

    def append_row(self, row, value_input_option=None):
        self.rows.append(list(row))

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.rows.append(list(r))

    def delete_rows(self, index, count=1):
        del self.rows[index - 1 : index - 1 + count]

    def batch_update(self, updates, value_input_option=None):
        for u in updates:
            rng = u["range"]
            match = re.match(r"([A-Za-z]+)(\d+)", rng)
            if match:
                col_letter, row_str = match.groups()
                col_idx = ord(col_letter.upper()) - ord("A")
                row_idx = int(row_str) - 1
                val = u["values"][0][0]
                while len(self.rows) <= row_idx:
                    self.rows.append([])
                while len(self.rows[row_idx]) <= col_idx:
                    self.rows[row_idx].append("")
                self.rows[row_idx][col_idx] = val

    def update(self, range_name, values, value_input_option=None):
        match = re.match(r"([A-Za-z]+)(\d+)", range_name)
        if match:
            col_letter, row_str = match.groups()
            col_idx = ord(col_letter.upper()) - ord("A")
            row_idx = int(row_str) - 1
            val = values[0][0]
            while len(self.rows) <= row_idx:
                self.rows.append([])
            while len(self.rows[row_idx]) <= col_idx:
                self.rows[row_idx].append("")
            self.rows[row_idx][col_idx] = val


class MockSpreadsheet:
    def __init__(self, worksheets_dict):
        self.worksheets_dict = worksheets_dict

    def worksheet(self, title):
        if title not in self.worksheets_dict:
            raise KeyError(f"Worksheet '{title}' not found")
        return self.worksheets_dict[title]


@pytest.fixture
def mock_sheets_env():
    ws_lots = MockWorksheet(
        TAB_INVENTORY_LOTS,
        headers=LOTS_HEADERS,
        initial_rows=[
            ["LOT-001", "2026-10-15", "Goodwill", "120.00", "24", "5.00", "0", "Active", "Haul 1"],
            ["LOT-002", "2026-10-19", "Estate Sale", "200.00", "20", "10.00", "0", "Active", "Haul 2"],
        ],
    )
    ws_listings = MockWorksheet(
        TAB_ITEM_LISTINGS,
        headers=LISTINGS_HEADERS,
        initial_rows=[
            [
                "SKU-1001",
                "LOT-001",
                "Vintage Carhartt Jacket",
                "Carhartt",
                "Outerwear",
                "=IFERROR(VLOOKUP(B2, Inventory_Lots!A:F, 6, FALSE), 0)",
                "75.00",
                "70.00",
                "15",
                "120",
                "vinted_111",
                "https://vinted.com/items/111",
                "Listed",
                "2026-10-15",
                "2026-10-19 12:00:00",
            ],
            [
                "SKU-1002",
                "",
                "Nike Swoosh Hoodie",
                "Nike",
                "Sweaters & Hoodies",
                "=IFERROR(VLOOKUP(B3, Inventory_Lots!A:F, 6, FALSE), 0)",
                "45.00",
                "40.00",
                "8",
                "65",
                "vinted_222",
                "https://vinted.com/items/222",
                "Listed",
                "2026-10-19",
                "2026-10-19 12:00:00",
            ],
        ],
    )
    ws_sold = MockWorksheet(TAB_SOLD_ITEMS, headers=SOLD_ITEMS_HEADERS)
    ws_sales = MockWorksheet(TAB_SALES_LOG, headers=SALES_HEADERS)

    spreadsheet = MockSpreadsheet({
        TAB_INVENTORY_LOTS: ws_lots,
        TAB_ITEM_LISTINGS: ws_listings,
        TAB_SOLD_ITEMS: ws_sold,
        TAB_SALES_LOG: ws_sales,
    })

    client = MagicMock()
    client.get_spreadsheet.return_value = spreadsheet
    engine = SheetsSyncEngine(client)
    return engine, spreadsheet


def test_normalize_date_str():
    assert normalize_date_str("2026-10-19") == "2026-10-19"
    assert normalize_date_str("10/19/2026") == "2026-10-19"
    assert normalize_date_str("2026/10/19") == "2026-10-19"
    assert normalize_date_str("10-19-2026") == "2026-10-19"
    assert normalize_date_str("") == ""


def test_get_max_sku_num(mock_sheets_env):
    engine, sh = mock_sheets_env
    # Current listings have SKU-1001 and SKU-1002
    assert engine.get_max_sku_num() == 1002

    # Add SKU-1005 to Sold_Items
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.append_row(["SKU-1005"] + [""] * (len(SOLD_ITEMS_HEADERS) - 1))
    assert engine.get_max_sku_num() == 1005


def test_auto_assign_lots_by_date(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # SKU-1002 has empty Lot_ID and Date_Listed="2026-10-19", which matches LOT-002
    assert ws_listings.rows[2][1] == ""
    assigned_count = engine.auto_assign_lots_by_date()
    assert assigned_count == 1
    assert ws_listings.rows[2][1] == "LOT-002"


def test_move_listing_to_sold(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Initially 2 active listings, 0 sold items
    assert len(ws_listings.rows) == 3  # Header + 2 items
    assert len(ws_sold.rows) == 1  # Header only

    moved = engine.move_listing_to_sold(sku="SKU-1001", status="Sold (Vinted)")
    assert moved is not None
    assert moved["Item_SKU"] == "SKU-1001"
    assert moved["Title"] == "Vintage Carhartt Jacket"
    assert moved["Status"] == "Sold (Vinted)"

    # Now active listings should have 1 item left (SKU-1002)
    assert len(ws_listings.rows) == 2
    assert ws_listings.rows[1][0] == "SKU-1002"

    # Sold_Items should have the moved row
    assert len(ws_sold.rows) == 2
    assert ws_sold.rows[1][0] == "SKU-1001"
    assert ws_sold.rows[1][12] == "Sold (Vinted)"


def test_reconcile_sold_listings(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Manually change SKU-1002 status in Item_Listings to 'Sold'
    ws_listings.rows[2][12] = "Sold"

    reconciled = engine.reconcile_sold_listings()
    assert reconciled == 1

    # SKU-1002 should now be removed from Item_Listings and present in Sold_Items
    assert len(ws_listings.rows) == 2
    assert ws_listings.rows[1][0] == "SKU-1001"
    assert len(ws_sold.rows) == 2
    assert ws_sold.rows[1][0] == "SKU-1002"


def test_sync_listings_moves_sold_and_adds_active(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    scraped_items = [
        # Existing item that just sold on Vinted
        ItemListing(
            item_sku="",
            title="Vintage Carhartt Jacket",
            brand="Carhartt",
            category="Outerwear",
            current_price=70.00,
            favorites=20,
            views=150,
            vinted_id="vinted_111",
            status="Sold",
            date_listed=date(2026, 10, 15),
        ),
        # Brand new active listing
        ItemListing(
            item_sku="",
            title="Patagonia Synchilla Fleece",
            brand="Patagonia",
            category="Outerwear",
            current_price=55.00,
            favorites=5,
            views=30,
            vinted_id="vinted_333",
            status="Listed",
            date_listed=date(2026, 10, 19),
        ),
    ]

    res = engine.sync_listings(scraped_items)
    assert res["moved_to_sold"] >= 1
    assert res["created"] >= 1

    # Vintage Carhartt Jacket moved to Sold_Items
    assert any(r[0] == "SKU-1001" for r in ws_sold.rows[1:])
    # Active listings does NOT contain Vintage Carhartt Jacket
    assert not any(r[0] == "SKU-1001" for r in ws_listings.rows[1:])
    # Active listings contains new Patagonia item with SKU-1003
    assert any("Patagonia Synchilla" in r[2] for r in ws_listings.rows[1:])


def test_sync_orders_moves_listing_and_logs_sale(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sales = sh.worksheet(TAB_SALES_LOG)

    order = SalesOrder(
        order_id="ORD-7788",
        item_sku="SKU-1001",
        item_title="Vintage Carhartt Jacket",
        sale_date=date(2026, 10, 19),
        sold_price=70.00,
        seller_fees=0.0,
        net_payout=70.00,
        item_cost=0.0,
        packaging_cost=0.0,
        vinted_status="Completed",
    )

    res = engine.sync_orders([order])
    assert res["synced_orders"] == 1

    # Carhartt jacket should be moved from Item_Listings to Sold_Items
    assert not any(r[0] == "SKU-1001" for r in ws_listings.rows[1:])
    assert any(r[0] == "SKU-1001" for r in ws_sold.rows[1:])

    # Sales_Log should have the order
    assert len(ws_sales.rows) == 2
    sales_entry = ws_sales.rows[1]
    assert sales_entry[0] == "ORD-7788"
    assert sales_entry[2] == "SKU-1001"
    # Concrete brand extracted from moved item
    assert sales_entry[4] == "Carhartt"
    # Formula for Item_Cost references Sold_Items first
    assert "Sold_Items!A:F" in sales_entry[11]


def test_sync_lots_updates_formulas_and_status(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_lots = sh.worksheet(TAB_INVENTORY_LOTS)

    # Initially LOT-001 has 1 item in active listings (SKU-1001)
    # LOT-002 has 0 items
    res = engine.sync_lots()
    assert res["total_lots"] == 2
    assert res["active_lots"] == 1
    assert res["completed_lots"] == 0

    # Verify formula insertion for row 2 (LOT-001)
    lot_1_row = ws_lots.rows[1]
    assert "D2/E2" in lot_1_row[5]
    assert "COUNTIF(Item_Listings!B:B, A2)" in lot_1_row[6]
    assert "Completed" in lot_1_row[7]


def test_sync_lots_marks_completed_when_all_sold(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_lots = sh.worksheet(TAB_INVENTORY_LOTS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Set LOT-001 Total_Articles to 1
    ws_lots.rows[1][4] = "1"

    # Add 1 sold item belonging to LOT-001 in Sold_Items
    ws_sold.append_row([
        "SKU-1001", "LOT-001", "Vintage Jacket", "Carhartt", "Outerwear",
        "5.00", "70.00", "70.00", "15", "120", "v_111", "url", "Sold", "2026-10-15", "now"
    ])

    res = engine.sync_lots()
    assert res["completed_lots"] == 1

    # Check status formula for LOT-001
    lot_1_row = ws_lots.rows[1]
    assert "Completed" in lot_1_row[7]
