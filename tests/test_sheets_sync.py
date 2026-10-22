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
    TAB_SHIPPING_SUPPLIES,
    LISTINGS_HEADERS,
    SOLD_ITEMS_HEADERS,
    SALES_HEADERS,
    LOTS_HEADERS,
    SUPPLIES_HEADERS,
)
from src.models import ItemListing, SalesOrder
from src.sheets.client import auto_provision_user_sheet


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

    def update(self, *args, **kwargs):
        values = kwargs.get("values")
        range_name = kwargs.get("range_name")
        if not range_name and len(args) > 0:
            range_name = args[0]
        if values is None and len(args) > 1:
            values = args[1]
        if isinstance(values, str) and isinstance(range_name, list):
            range_name, values = values, range_name
        match = re.match(r"([A-Za-z]+)(\d+)(?::([A-Za-z]+)(\d+))?", range_name or "")
        if match:
            start_col, start_row, end_col, end_row = match.groups()
            c_start = ord(start_col.upper()) - ord("A")
            r_start = int(start_row) - 1
            for r_offset, row_vals in enumerate(values or []):
                curr_r = r_start + r_offset
                while len(self.rows) <= curr_r:
                    self.rows.append([])
                for c_offset, v in enumerate(row_vals):
                    curr_c = c_start + c_offset
                    while len(self.rows[curr_r]) <= curr_c:
                        self.rows[curr_r].append("")
                    self.rows[curr_r][curr_c] = v


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
                "YES",
                "5.00",
                "10/18: $75.00 → $70.00",
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
                "YES",
                "5.00",
                "10/18: $45.00 → $40.00",
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
    ws_supplies = MockWorksheet(
        TAB_SHIPPING_SUPPLIES,
        headers=SUPPLIES_HEADERS,
        initial_rows=[
            ["SUP-01", "2026-10-01", "10x13 Poly Mailers", "Amazon", "100", "18.99", "0.19", "0.24"],
            ["SUP-02", "", "", "", "", "", "", ""],
        ],
    )

    spreadsheet = MockSpreadsheet({
        TAB_INVENTORY_LOTS: ws_lots,
        TAB_ITEM_LISTINGS: ws_listings,
        TAB_SOLD_ITEMS: ws_sold,
        TAB_SALES_LOG: ws_sales,
        TAB_SHIPPING_SUPPLIES: ws_supplies,
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

    moved = engine.move_listing_to_sold(sku="SKU-1001", status="Sold (Vinted)", sold_price=68.00)
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
    status_idx = SOLD_ITEMS_HEADERS.index("Status")
    sold_price_idx = SOLD_ITEMS_HEADERS.index("Sold_Price")
    assert ws_sold.rows[1][status_idx] == "Sold (Vinted)"
    assert ws_sold.rows[1][sold_price_idx] == 68.00


def test_reconcile_sold_listings(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    status_idx = LISTINGS_HEADERS.index("Status")
    # Manually change SKU-1002 status in Item_Listings to 'Sold'
    ws_listings.rows[2][status_idx] = "Sold"

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

    # Sold_Items should contain the full transaction details
    assert len(ws_sold.rows) == 2
    sold_entry = ws_sold.rows[1]
    assert sold_entry[0] == "SKU-1001"
    assert sold_entry[2] == "Vintage Carhartt Jacket"
    assert sold_entry[3] == "Carhartt"
    # Sold_Price
    assert sold_entry[11] == 70.00
    # Price_Discount formula
    assert "G2-L2" in sold_entry[12]
    # Sale_Type formula
    assert "Original Price" in sold_entry[13]
    # Platform
    assert sold_entry[15] == "Vinted"
    # Net_Profit formula deducting Unit_Cost and Packaging_Cost with zero seller fees
    assert "L2-F2-Q2" in sold_entry[17]
    # Order_ID
    assert sold_entry[21] == "ORD-7788"


def test_sale_type_formula_and_offer_handling(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # SKU-1002 starts with Posted_Price=45.00, Current_Price=40.00
    # Simulate an accepted buyer offer at $36.00 (below both posted and listed price)
    moved = engine.move_listing_to_sold(
        sku="SKU-1002",
        status="Sold (Vinted)",
        sold_price=36.00,
        platform="Vinted",
        order_id="ORD-OFFER-1",
    )
    assert moved is not None
    assert moved["Sold_Price"] == 36.00
    # Sale_Type formula handles Accepted Offer, Lowered Price, and Original Price
    assert "Accepted Offer" in moved["Sale_Type"]
    assert "Lowered Price" in moved["Sale_Type"]
    assert "Original Price" in moved["Sale_Type"]


def test_price_drop_detection_and_history_logging(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # SKU-1002 (Nike Swoosh Hoodie) starts with Posted_Price=45.00, Current_Price=40.00
    # Now simulate Vinted scraper detecting a price reduction to $35.00
    scraped_items = [
        ItemListing(
            item_sku="SKU-1002",
            title="Nike Swoosh Hoodie",
            brand="Nike",
            category="Sweaters & Hoodies",
            posted_price=45.00,
            current_price=35.00,
            favorites=12,
            views=90,
            vinted_id="vinted_222",
            status="Listed",
            date_listed=date(2026, 10, 19),
        )
    ]

    res = engine.sync_listings(scraped_items)
    assert res["updated"] == 1

    # Check that Current_Price updated to 35.00
    nike_row = ws_listings.rows[2]
    curr_price_idx = LISTINGS_HEADERS.index("Current_Price")
    hist_idx = LISTINGS_HEADERS.index("Price_History")
    posted_idx = LISTINGS_HEADERS.index("Posted_Price")

    assert nike_row[curr_price_idx] == 35.00
    assert nike_row[posted_idx] == "45.00"
    # Price_History should contain the new drop entry
    assert "$40.00 → $35.00" in nike_row[hist_idx]


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
    row_data = [""] * len(SOLD_ITEMS_HEADERS)
    row_data[0] = "SKU-1001"
    row_data[1] = "LOT-001"
    row_data[2] = "Vintage Jacket"
    row_data[3] = "Carhartt"
    row_data[4] = "Outerwear"
    row_data[5] = "5.00"
    row_data[6] = "75.00"
    row_data[7] = "70.00"
    row_data[11] = "70.00"  # Sold_Price
    row_data[16] = "Sold"

    ws_sold.append_row(row_data)

    res = engine.sync_lots()
    assert res["completed_lots"] == 1

    # Check status formula for LOT-001
    lot_1_row = ws_lots.rows[1]
    assert "Completed" in lot_1_row[7]


def test_get_all_lots_and_add_inventory_lot(mock_sheets_env):
    engine, sh = mock_sheets_env
    lots = engine.get_all_lots()
    assert len(lots) == 2
    assert lots[0]["Lot_ID"] == "LOT-001"
    assert lots[1]["Lot_ID"] == "LOT-002"

    # Test next lot ID generation
    next_id = engine.get_next_lot_id()
    assert next_id == "LOT-003"

    # Add new inventory lot
    res = engine.add_inventory_lot(
        lot_id="LOT-003",
        purchase_date="2026-10-21",
        source_vendor="Goodwill Haul",
        total_cost=75.0,
        total_articles=30,
        status="Active",
        notes="30 graphic tees",
    )
    assert res["status"] == "created"
    assert res["lot_id"] == "LOT-003"

    # Verify lot was appended with formulas
    ws_lots = sh.worksheet(TAB_INVENTORY_LOTS)
    assert len(ws_lots.rows) == 4
    lot_3_row = ws_lots.rows[3]
    assert lot_3_row[0] == "LOT-003"
    assert lot_3_row[2] == "Goodwill Haul"
    assert lot_3_row[3] == 75.0
    assert lot_3_row[4] == 30
    assert "D4/E4" in lot_3_row[5]
    assert "COUNTIF(Item_Listings!B:B, A4)" in lot_3_row[6]

    # Verify duplicate creation protection
    res_dup = engine.add_inventory_lot(lot_id="LOT-003")
    assert res_dup["status"] == "exists"

    # Verify next lot ID is now LOT-004
    assert engine.get_next_lot_id() == "LOT-004"


def test_get_all_supplies_and_save_shipping_supply(mock_sheets_env):
    engine, sh = mock_sheets_env

    # 1. Fetch current supplies
    sup_data = engine.get_all_supplies()
    assert len(sup_data["supplies"]) == 1
    assert sup_data["supplies"][0]["Supply_ID"] == "SUP-01"
    assert sup_data["avg_cost_per_shipment"] == 0.24

    # 2. Next suggested supply ID should find the empty placeholder SUP-02
    next_id = engine.get_next_supply_id()
    assert next_id == "SUP-02"

    # 3. Save to SUP-02
    res_update = engine.save_shipping_supply(
        supply_id="SUP-02",
        purchase_date="2026-10-21",
        item_description="Thermal Shipping Labels (500ct)",
        vendor="Amazon",
        quantity=500,
        total_cost=15.00,
    )
    assert res_update["status"] == "updated"
    assert res_update["supply_id"] == "SUP-02"

    # Verify updated row
    ws_sup = sh.worksheet(TAB_SHIPPING_SUPPLIES)
    row_2 = ws_sup.rows[2]
    assert row_2[0] == "SUP-02"
    assert row_2[2] == "Thermal Shipping Labels (500ct)"
    assert row_2[4] == 500
    assert row_2[5] == 15.00

    # 4. Save a brand new supply SUP-03
    res_create = engine.save_shipping_supply(
        supply_id="SUP-03",
        purchase_date="2026-10-21",
        item_description="Bubble Wrap 12x100ft",
        vendor="Uline",
        quantity=1,
        total_cost=22.50,
    )
    assert res_create["status"] == "created"
    assert len(ws_sup.rows) == 4
    row_3 = ws_sup.rows[3]
    assert row_3[0] == "SUP-03"
    assert "F4/E4" in row_3[6]
    assert "H$2" in row_3[7]


def test_auto_provision_user_sheet_discovered(monkeypatch):
    mock_existing = MagicMock()
    mock_existing.id = "discovered_sheet_123"
    mock_existing.title = "SpreadThrift - Personal Tracker"

    mock_client = MagicMock()
    mock_client.find_sheet_by_title.return_value = mock_existing

    monkeypatch.setattr("src.sheets.client.GoogleSheetsClient", lambda credentials: mock_client)

    s_id, s_title, action = auto_provision_user_sheet(MagicMock(), user_name="Alex")
    assert s_id == "discovered_sheet_123"
    assert s_title == "SpreadThrift - Personal Tracker"
    assert action == "discovered"


def test_auto_provision_user_sheet_cloned(monkeypatch):
    mock_cloned = MagicMock()
    mock_cloned.id = "cloned_sheet_456"
    mock_cloned.title = "Thread Count - Vinted Sales & Inventory Tracker"

    mock_gc = MagicMock()
    mock_gc.copy.return_value = mock_cloned

    mock_client = MagicMock()
    mock_client.authenticate.return_value = mock_gc
    mock_client.find_sheet_by_title.return_value = None

    monkeypatch.setattr("src.sheets.client.GoogleSheetsClient", lambda credentials: mock_client)

    s_id, s_title, action = auto_provision_user_sheet(MagicMock(), user_name="Alex")
    assert s_id == "cloned_sheet_456"
    assert action == "cloned"


def test_auto_provision_user_sheet_fallback_create(monkeypatch):
    mock_created = MagicMock()
    mock_created.id = "new_created_sheet_789"
    mock_created.title = "Thread Count - Vinted Sales & Inventory Tracker"

    mock_gc = MagicMock()
    mock_gc.copy.side_effect = RuntimeError("Drive quota or permission error")
    mock_gc.create.return_value = mock_created

    mock_client = MagicMock()
    mock_client.authenticate.return_value = mock_gc
    mock_client.find_sheet_by_title.return_value = None

    monkeypatch.setattr("src.sheets.client.GoogleSheetsClient", lambda credentials: mock_client)
    monkeypatch.setattr("src.sheets.templates.initialize_all_tabs", lambda c: None)

    s_id, s_title, action = auto_provision_user_sheet(MagicMock(), user_name="Alex")
    assert s_id == "new_created_sheet_789"
    assert action == "initialized"


def test_process_email_batch(mock_sheets_env):
    from datetime import datetime
    from src.email_parser import ParsedEmailEvent

    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # SKU-1001 is "Vintage Carhartt Jacket"
    events = [
        ParsedEmailEvent(
            event_type="favorite",
            item_title="Vintage Carhartt Jacket",
            vinted_id="1001",
            raw_subject="Item favorited",
            timestamp=datetime.now(),
        ),
        ParsedEmailEvent(
            event_type="offer",
            item_title="Vintage Carhartt Jacket",
            offer_price=55.0,
            listed_price=68.0,
            vinted_id="1001",
            raw_subject="New offer",
            timestamp=datetime.now(),
        ),
        ParsedEmailEvent(
            event_type="sale",
            item_title="Nike Track Pants",
            sold_price=35.0,
            order_id="ORD-EMAIL-TEST-1",
            raw_subject="Item sold",
            timestamp=datetime.now(),
        ),
    ]

    results = engine.process_email_batch(events)
    assert len(results) == 3
    assert results[0]["status"] == "favorite_incremented"
    assert results[1]["status"] == "offer_logged"
    assert results[2]["status"] in ("moved_to_sold", "appended_to_sold")


def test_sync_listings_with_unassigned_lot(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    item = ItemListing(
        item_sku="",
        title="Unassigned Brand New Hoodie",
        brand="Nike",
        category="Sweatshirts",
        current_price=45.00,
        lot_id="",
        status="Listed",
        date_listed=date(2026, 10, 19),  # Same date as LOT-001 in mock
    )

    # When auto_assign_lot=False, should NOT assign LOT-001 from date match
    res = engine.sync_listings([item], auto_assign_lot=False)
    assert res["created"] == 1

    matched = [r for r in ws_listings.rows[1:] if "Unassigned Brand New Hoodie" in r[2]]
    assert len(matched) == 1
    # Column 1 is Lot_ID -> must be empty
    assert matched[0][1] == ""


def test_sync_listings_preserves_edited_title_and_lot(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # Simulates an item edited in st.data_editor
    item = ItemListing(
        item_sku="",
        title="Corrected Vintage Leather Jacket",
        brand="Wilson's",
        category="Outerwear",
        current_price=120.00,
        lot_id="LOT-CUSTOM-99",
        status="Listed",
        date_listed=date(2026, 10, 19),
    )

    res = engine.sync_listings([item], auto_assign_lot=False)
    assert res["created"] == 1

    matched = [r for r in ws_listings.rows[1:] if r[2] == "Corrected Vintage Leather Jacket"]
    assert len(matched) == 1
    assert matched[0][1] == "LOT-CUSTOM-99"


def test_sync_orders_with_unassigned_lot(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    order = SalesOrder(
        order_id="ORD-UNASSIGNED-1",
        item_title="Standalone Direct Sale Sweater",
        sale_date=date(2026, 10, 19),  # Same date as LOT-001 in mock
        sold_price=40.00,
        vinted_status="Completed",
    )

    # When auto_assign_lot=False and default_lot="", should NOT assign LOT-001
    res = engine.sync_orders([order], default_lot="", auto_assign_lot=False)
    assert res["synced_orders"] == 1

    matched = [r for r in ws_sold.rows[1:] if r[2] == "Standalone Direct Sale Sweater"]
    assert len(matched) == 1
    assert matched[0][1] == ""


def test_clean_blank_rows(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

    # Insert a blank row and a row with no SKU or Title
    ws_listings.rows.append(["", "", "", "", "", ""])
    ws_listings.rows.append(["", "LOT-001", "", "", "", ""])
    initial_count = len(ws_listings.rows)

    res = engine.clean_blank_rows([TAB_ITEM_LISTINGS])
    assert res[TAB_ITEM_LISTINGS] == 2
    assert len(ws_listings.rows) == initial_count - 2


def test_process_email_batch_with_custom_lot_and_metadata(mock_sheets_env):
    from src.email_parser import ParsedEmailEvent
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Simulate an email sale with user-provided lot, brand, and category
    event = ParsedEmailEvent(
        event_type="sale",
        item_title="Floral Summer Dress",
        vinted_id="99881122",
        order_id="ORD-EMAIL-TEST-1",
        sold_price=28.00,
        lot_id="LOT-002",
        brand="Zara",
        category="Dresses & Skirts",
        vinted_url="https://www.vinted.com/items/99881122-floral-summer-dress",
    )

    results = engine.process_email_batch([event], default_lot="LOT-001")
    assert len(results) == 1
    assert results[0]["status"] == "appended_to_sold"
    assert results[0]["lot"] == "LOT-002"

    matched = [r for r in ws_sold.rows[1:] if r[2] == "Floral Summer Dress"]
    assert len(matched) == 1
    row = matched[0]
    assert row[1] == "LOT-002"            # Lot_ID
    assert row[3] == "Zara"               # Brand
    assert row[4] == "Dresses & Skirts"   # Category
    assert row[11] == 28.00               # Sold_Price


def test_reconcile_missing_sold_metadata(mock_sheets_env):
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Add a sold item row missing title, lot, brand, and category, but with a Vinted URL slug
    row_data = [""] * len(SOLD_ITEMS_HEADERS)
    row_data[0] = "SKU-9999"
    row_data[1] = ""                       # Missing Lot
    row_data[2] = ""                       # Missing Title
    row_data[3] = ""                       # Missing Brand
    row_data[4] = ""                       # Missing Category
    row_data[11] = 50.00                   # Sold_Price
    row_data[22] = "https://www.vinted.com/items/55443322-vintage-levi-denim-jacket"
    row_data[23] = "Sold"
    ws_sold.rows.append(row_data)

    res = engine.reconcile_missing_sold_metadata(default_lot="LOT-001")
    assert res["fixed_rows"] >= 1
    assert res["titles_fixed"] >= 1
    assert res["lots_assigned"] >= 1
    assert res["brands_fixed"] >= 1
    assert res["categories_fixed"] >= 1

    # Verify backfilled values
    matched = [r for r in ws_sold.rows[1:] if r[0] == "SKU-9999"]
    assert len(matched) == 1
    fixed_row = matched[0]
    assert fixed_row[1] == "LOT-001"
    assert fixed_row[2] == "Vintage Levi Denim Jacket"
    assert fixed_row[3] == "Levi's"
    assert fixed_row[4] == "Outerwear"


