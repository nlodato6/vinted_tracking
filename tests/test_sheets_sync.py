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
                "HTML",
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
                "HTML",
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


def test_reconcile_missing_sold_metadata_module_export_and_sync_engine(mock_sheets_env):
    from src.sheets import reconcile_missing_sold_metadata, SheetsSyncEngine
    from app_pages.sync import get_sheets_sync_engine

    engine, sh = mock_sheets_env
    assert hasattr(engine, "reconcile_missing_sold_metadata")
    assert callable(reconcile_missing_sold_metadata)

    # Test get_sheets_sync_engine from app_pages.sync
    app_engine = get_sheets_sync_engine(engine.client)
    assert hasattr(app_engine, "reconcile_missing_sold_metadata")
    assert hasattr(app_engine, "clean_blank_rows")


def test_reconcile_missing_sold_metadata_with_emails(mock_sheets_env, monkeypatch):
    from src.email_parser import ParsedEmailEvent
    import src.email_parser

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Row that was imported previously with only "Order <UUID>" and missing lot, brand, category, url
    row_data = [""] * len(SOLD_ITEMS_HEADERS)
    row_data[0] = "SKU-8888"
    row_data[1] = ""                                                       # Missing Lot
    row_data[2] = "Order 01a1569f-be78-78a1-a263-1fdb328d3780"           # Generic Order Title
    row_data[3] = ""                                                       # Missing Brand
    row_data[4] = ""                                                       # Missing Category
    row_data[11] = 68.00                                                   # Sold_Price
    row_data[14] = "2026-10-22"                                            # Sale_Date
    row_data[21] = "01a1569f-be78-78a1-a263-1fdb328d3780"                 # Order_ID
    row_data[22] = ""                                                       # Missing Vinted_URL
    row_data[23] = "Sold"
    ws_sold.rows.append(row_data)

    mock_email = ParsedEmailEvent(
        event_type="sale",
        item_title="Vintage Carhartt Chore Jacket",
        brand="Carhartt",
        category="Outerwear",
        order_id="01a1569f-be78-78a1-a263-1fdb328d3780",
        sold_price=68.00,
        vinted_url="https://www.vinted.com/transaction/01a1569f-be78-78a1-a263-1fdb328d3780",
        timestamp=datetime(2026, 10, 22, 12, 0, 0),
    )

    monkeypatch.setattr(
        src.email_parser,
        "fetch_vinted_emails_via_imap",
        lambda *args, **kwargs: [mock_email],
    )

    res = engine.reconcile_missing_sold_metadata(
        default_lot="LOT-OCTOBER",
        gmail_user="test@gmail.com",
        gmail_password="app_password",
        max_emails_scan=50,
    )

    assert res["fixed_rows"] == 1
    assert res["titles_fixed"] == 1
    assert res["lots_assigned"] == 1
    assert res["brands_fixed"] == 1
    assert res["categories_fixed"] == 1
    assert res["emails_scanned"] == 1
    assert res["emails_matched"] == 1

    matched = [r for r in ws_sold.rows[1:] if r[0] == "SKU-8888"]
    assert len(matched) == 1
    fixed_row = matched[0]
    assert fixed_row[1] == "LOT-OCTOBER"
    assert fixed_row[2] == "Vintage Carhartt Chore Jacket"
    assert fixed_row[3] == "Carhartt"
    assert fixed_row[4] == "Outerwear"
    assert fixed_row[21] == "01a1569f-be78-78a1-a263-1fdb328d3780"
    assert fixed_row[22] == "https://www.vinted.com/transaction/01a1569f-be78-78a1-a263-1fdb328d3780"


def test_email_then_html_sync_orders_deduplicates_and_enriches(mock_sheets_env):
    """
    When a sold entry is first pulled from email (missing full title, lot, brand, category),
    and later HTML orders are imported, sync_orders MUST NOT append a duplicate row.
    It must detect the duplicate and enrich the existing row with the additional fields.
    """
    from datetime import datetime, date
    from src.email_parser import ParsedEmailEvent
    from src.models import SalesOrder

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # 1. Email arrives with placeholder title and missing lot/brand/category
    email_event = ParsedEmailEvent(
        event_type="sale",
        item_title="Order 01a156e8-07ba-4ba8-9b88-c44d03613a0c",
        order_id="01a156e8-07ba-4ba8-9b88-c44d03613a0c",
        sold_price=45.00,
        timestamp=datetime(2026, 10, 21, 14, 30),
    )
    email_res = engine.process_email_batch([email_event])
    assert len(email_res) == 1
    assert email_res[0]["status"] == "appended_to_sold"
    assert len(ws_sold.rows) == 2  # Header + 1 row

    initial_row = ws_sold.rows[1]
    assert initial_row[2] == "Order 01a156e8-07ba-4ba8-9b88-c44d03613a0c"
    assert initial_row[1] == ""  # Lot_ID empty

    # 2. Later, HTML orders are imported with rich metadata
    html_order = SalesOrder(
        order_id="01a156e8-07ba-4ba8-9b88-c44d03613a0c",
        item_title="Patagonia Synchilla Fleece Snap-T Pullover",
        brand="Patagonia",
        category="Fleece",
        sold_price=45.00,
        sale_date=date(2026, 10, 21),
        favorites=12,
        platform="Vinted",
    )
    order_res = engine.sync_orders([html_order], default_lot="LOT-002")
    assert order_res["synced_orders"] == 1

    # CRITICAL: Duplicate check must NOT append a second row!
    assert len(ws_sold.rows) == 2

    # The existing row must now contain the enriched fields from HTML
    enriched_row = ws_sold.rows[1]
    assert enriched_row[0] == initial_row[0]  # SKU preserved
    assert enriched_row[1] == "LOT-002"       # Lot_ID enriched
    assert enriched_row[2] == "Patagonia Synchilla Fleece Snap-T Pullover"  # Title enriched!
    assert enriched_row[3] == "Patagonia"     # Brand enriched!
    assert enriched_row[4] == "Fleece"        # Category enriched!
    assert enriched_row[11] == 45.00          # Price preserved
    assert enriched_row[19] == 12             # Favorites enriched!
    assert enriched_row[21] == "01a156e8-07ba-4ba8-9b88-c44d03613a0c"


def test_email_fallback_order_id_fuzzy_matched_and_upgraded_by_html(mock_sheets_env):
    """
    When an email sale had an ORD-timestamp fallback order_id and placeholder title,
    HTML orders matching price and date must detect the duplicate, prevent a new row,
    and upgrade the Order_ID, Title, Brand, and Category.
    """
    from datetime import datetime, date
    from src.email_parser import ParsedEmailEvent
    from src.models import SalesOrder

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Email with fallback ORD- timestamp
    email_event = ParsedEmailEvent(
        event_type="sale",
        item_title="Order ORD-1729500000",
        order_id="ORD-1729500000",
        sold_price=30.00,
        timestamp=datetime(2026, 10, 21, 10, 0),
    )
    engine.process_email_batch([email_event])
    assert len(ws_sold.rows) == 2

    # HTML order with real UUID and full title
    html_order = SalesOrder(
        order_id="01a156e8-abcd-4ba8-9b88-c44d03613a0c",
        item_title="Vintage Carhartt Work Jacket",
        brand="Carhartt",
        category="Outerwear",
        sold_price=30.00,
        sale_date=date(2026, 10, 21),
    )
    res = engine.sync_orders([html_order])
    assert res["synced_orders"] == 1

    # No duplicate row
    assert len(ws_sold.rows) == 2
    row = ws_sold.rows[1]
    assert row[2] == "Vintage Carhartt Work Jacket"
    assert row[3] == "Carhartt"
    assert row[4] == "Outerwear"
    assert row[21] == "01a156e8-abcd-4ba8-9b88-c44d03613a0c"  # Upgraded from ORD-1729500000!


def test_html_first_then_email_sync_deduplicates_and_enriches(mock_sheets_env):
    """
    When HTML orders was imported first, and email sync runs later for the same sale,
    email sync must NOT append a duplicate row and must enrich any missing fields (like Vinted_URL).
    """
    from datetime import datetime, date
    from src.email_parser import ParsedEmailEvent
    from src.models import SalesOrder

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # 1. HTML order imported first (URL empty)
    html_order = SalesOrder(
        order_id="01a156e8-8888-4ba8-9b88-c44d03613a0c",
        item_title="Vintage Nike Crewneck Sweatshirt",
        brand="Nike",
        category="Hoodies & Sweatshirts",
        sold_price=38.00,
        sale_date=date(2026, 10, 21),
    )
    engine.sync_orders([html_order], default_lot="")
    assert len(ws_sold.rows) == 2
    assert ws_sold.rows[1][22] == ""  # Vinted_URL empty

    # 2. Email arrives with URL and user assigns a lot in email preview
    email_event = ParsedEmailEvent(
        event_type="sale",
        item_title="Vintage Nike Crewneck Sweatshirt",
        order_id="01a156e8-8888-4ba8-9b88-c44d03613a0c",
        sold_price=38.00,
        vinted_url="https://www.vinted.com/items/776655-vintage-nike-crewneck",
        lot_id="LOT-001",
        timestamp=datetime(2026, 10, 21, 12, 0),
    )
    email_res = engine.process_email_batch([email_event])
    assert len(email_res) == 1
    assert email_res[0]["status"] == "enriched_sold"

    # No duplicate row
    assert len(ws_sold.rows) == 2
    enriched_row = ws_sold.rows[1]
    assert enriched_row[1] == "LOT-001"
    assert enriched_row[2] == "Vintage Nike Crewneck Sweatshirt"
    assert enriched_row[22] == "https://www.vinted.com/items/776655-vintage-nike-crewneck"


def test_closet_sales_sync_listings_deduplicates_and_enriches_email_row(mock_sheets_env):
    """
    When an email sale created a row with partial title and no brand/category,
    sync_listings from member profile sold HTML must match by Vinted ID / URL,
    prevent duplicate rows, and enrich Title, Brand, and Category.
    """
    from datetime import date
    from src.models import ItemListing

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Existing placeholder row from email in Sold_Items
    placeholder_sold_row = [""] * len(SOLD_ITEMS_HEADERS)
    placeholder_sold_row[0] = "SKU-9999"
    placeholder_sold_row[1] = ""
    placeholder_sold_row[2] = "Vinted Item 554433"
    placeholder_sold_row[3] = "Unbranded"
    placeholder_sold_row[4] = "Other Apparel"
    placeholder_sold_row[11] = 25.00
    placeholder_sold_row[14] = "2026-10-20"
    placeholder_sold_row[22] = "https://www.vinted.com/items/554433"
    placeholder_sold_row[23] = "Sold"
    ws_sold.append_row(placeholder_sold_row)
    assert len(ws_sold.rows) == 2

    # Scraped closet sold item
    closet_item = ItemListing(
        item_sku="",
        vinted_id="554433",
        title="Levi's Vintage 501 Straight Leg Jeans",
        brand="Levi's",
        category="Jeans",
        current_price=25.00,
        sold_price=25.00,
        status="Sold",
        vinted_url="https://www.vinted.com/items/554433-levis-vintage-501",
        date_listed=date(2026, 10, 1),
    )

    res = engine.sync_listings([closet_item])
    assert res["created"] == 0  # No new listing created!
    assert len(ws_sold.rows) == 2  # NO DUPLICATE ROW ADDED!

    enriched = ws_sold.rows[1]
    assert enriched[0] == "SKU-9999"
    assert enriched[2] == "Levi's Vintage 501 Straight Leg Jeans"  # Title enriched!
    assert enriched[3] == "Levi's"  # Brand enriched!
    assert enriched[4] == "Jeans"   # Category enriched!
    assert enriched[24] == "2026-10-01"  # Date_Listed enriched!


def test_find_duplicate_groups_by_order_id(mock_sheets_env):
    """Test find_duplicate_groups groups rows sharing an Order ID and scores completeness."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Row 2: Incomplete placeholder row from email
    row_a = [""] * len(SOLD_ITEMS_HEADERS)
    row_a[0] = "SKU-101"
    row_a[2] = "Order sold"  # placeholder
    row_a[6] = 45.00
    row_a[11] = 45.00
    row_a[14] = "2026-10-18"
    row_a[21] = "ORD-998877"  # Order ID
    ws_sold.append_row(row_a)

    # Row 3: Enriched complete row from HTML import
    row_b = [""] * len(SOLD_ITEMS_HEADERS)
    row_b[0] = "SKU-102"
    row_b[1] = "LOT-001"
    row_b[2] = "Patagonia Better Sweater Fleece Quarter Zip"
    row_b[3] = "Patagonia"
    row_b[4] = "Fleece"
    row_b[6] = 45.00
    row_b[11] = 45.00
    row_b[14] = "2026-10-18"
    row_b[21] = "ORD-998877"  # Order ID match!
    row_b[22] = "https://www.vinted.com/items/776655"
    ws_sold.append_row(row_b)

    groups = engine.find_duplicate_groups(tab_name=TAB_SOLD_ITEMS)
    assert len(groups) == 1
    g = groups[0]
    assert "Matching Order ID" in g["reason"]
    assert len(g["rows"]) == 2
    # Row 3 is more complete than Row 2, so it should be the recommended keep row
    assert g["recommended_keep_row_idx"] == 3


def test_find_duplicate_groups_by_price_date_placeholder(mock_sheets_env):
    """Test duplicate detection matches placeholder rows with real items by price and date."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Row 2: Real title
    row_a = [""] * len(SOLD_ITEMS_HEADERS)
    row_a[0] = "SKU-201"
    row_a[2] = "Carhartt Detroit Duck Canvas Jacket"
    row_a[3] = "Carhartt"
    row_a[4] = "Jackets & Coats"
    row_a[11] = 75.00
    row_a[14] = "2026-10-19"
    ws_sold.append_row(row_a)

    # Row 3: Placeholder title with same price & date
    row_b = [""] * len(SOLD_ITEMS_HEADERS)
    row_b[0] = "SKU-202"
    row_b[2] = "Vinted Item"
    row_b[11] = 75.00
    row_b[14] = "2026-10-19"
    ws_sold.append_row(row_b)

    groups = engine.find_duplicate_groups(tab_name=TAB_SOLD_ITEMS)
    assert len(groups) == 1
    assert groups[0]["recommended_keep_row_idx"] == 2


def test_resolve_duplicates_keep_row(mock_sheets_env):
    """Test resolve_duplicates removes the discarded row when action is keep_row."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    row_a = [""] * len(SOLD_ITEMS_HEADERS)
    row_a[0] = "SKU-1"
    row_a[2] = "Nike Hoodie"
    row_a[11] = 20.00
    row_a[21] = "ORD-001"
    ws_sold.append_row(row_a)

    row_b = [""] * len(SOLD_ITEMS_HEADERS)
    row_b[0] = "SKU-2"
    row_b[2] = "Nike Hoodie"
    row_b[11] = 20.00
    row_b[21] = "ORD-001"
    ws_sold.append_row(row_b)

    assert len(ws_sold.rows) == 3  # headers + 2 rows

    res = engine.resolve_duplicates(
        tab_name=TAB_SOLD_ITEMS,
        decisions=[{
            "group_id": "group_1",
            "action": "keep_row",
            "keep_row_idx": 2,
            "discard_row_indices": [3],
        }],
    )

    assert res["deleted_count"] == 1
    assert len(ws_sold.rows) == 2
    assert ws_sold.rows[1][0] == "SKU-1"


def test_resolve_duplicates_merge_into(mock_sheets_env):
    """Test resolve_duplicates enriches the kept row with missing fields before deleting the duplicate."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Row 2: Has Title and Price, but missing Brand and Category
    row_a = [""] * len(SOLD_ITEMS_HEADERS)
    row_a[0] = "SKU-10"
    row_a[1] = "LOT-001"
    row_a[2] = "Adidas Samba OG"
    row_a[3] = ""  # Missing brand
    row_a[4] = ""  # Missing category
    row_a[11] = 50.00
    row_a[14] = "2026-10-15"
    row_a[21] = "ORD-SAMBA"
    ws_sold.append_row(row_a)

    # Row 3: Incomplete title, but has Brand and Category
    row_b = [""] * len(SOLD_ITEMS_HEADERS)
    row_b[0] = "SKU-11"
    row_b[1] = ""
    row_b[2] = "Vinted Item"
    row_b[3] = "Adidas"
    row_b[4] = "Shoes"
    row_b[11] = 50.00
    row_b[14] = "2026-10-15"
    row_b[21] = "ORD-SAMBA"
    ws_sold.append_row(row_b)

    res = engine.resolve_duplicates(
        tab_name=TAB_SOLD_ITEMS,
        decisions=[{
            "group_id": "group_1",
            "action": "merge_into",
            "keep_row_idx": 2,
            "discard_row_indices": [3],
        }],
    )

    assert res["deleted_count"] == 1
    assert res["merged_count"] >= 1
    assert len(ws_sold.rows) == 2
    # Row 2 kept, and its brand and category were enriched!
    kept = ws_sold.rows[1]
    assert kept[2] == "Adidas Samba OG"
    assert kept[3] == "Adidas"
    assert kept[4] == "Shoes"


def test_resolve_duplicates_keep_both(mock_sheets_env):
    """Test resolve_duplicates preserves all rows when action is keep_both."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    row_a = [""] * len(SOLD_ITEMS_HEADERS)
    row_a[0] = "SKU-1"
    row_a[2] = "Vintage Tee"
    row_a[11] = 15.00
    ws_sold.append_row(row_a)

    row_b = [""] * len(SOLD_ITEMS_HEADERS)
    row_b[0] = "SKU-2"
    row_b[2] = "Vintage Tee"
    row_b[11] = 15.00
    ws_sold.append_row(row_b)

    assert len(ws_sold.rows) == 3

    res = engine.resolve_duplicates(
        tab_name=TAB_SOLD_ITEMS,
        decisions=[{
            "group_id": "group_1",
            "action": "keep_both",
            "keep_row_idx": None,
            "discard_row_indices": [],
        }],
    )

    assert res["deleted_count"] == 0
    assert res["kept_both_count"] == 1
    assert len(ws_sold.rows) == 3  # Both rows still intact!


def test_process_email_batch_with_none_fields_and_assigned_lot(mock_sheets_env):
    """Test process_email_batch handles events and sheet cells containing None without raising AttributeError."""
    from src.email_parser import ParsedEmailEvent
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Existing row in sheet with None in several empty cells (common in gspread)
    existing_row = [None] * len(SOLD_ITEMS_HEADERS)
    existing_row[0] = "SKU-500"
    existing_row[2] = "Old Item"
    existing_row[11] = 30.00
    existing_row[14] = "2026-10-20"
    ws_sold.append_row(existing_row)

    # Incoming email event with None for vinted_id, vinted_url, brand, category, but user assigned lot_id
    event = ParsedEmailEvent(
        event_type="sale",
        timestamp=datetime(2026, 10, 25, 12, 0, 0),
        vinted_id=None,
        vinted_url=None,
        item_title="Vintage Leather Bomber Jacket",
        sold_price=65.00,
        order_id="ORD-20261025-1122",
        buyer_username="buyer123",
        brand=None,
        category=None,
        lot_id="LOT-002",
    )

    results = engine.process_email_batch([event], default_lot="LOT-001")
    assert len(results) == 1
    assert results[0]["status"] == "appended_to_sold"
    assert results[0]["lot"] == "LOT-002"
    assert len(ws_sold.rows) == 3
    added_row = ws_sold.rows[2]
    assert added_row[1] == "LOT-002"  # Lot_ID
    assert added_row[2] == "Vintage Leather Bomber Jacket"


def test_find_duplicate_groups_has_lot_and_lot_id(mock_sheets_env):
    """Ensure find_duplicate_groups returns both 'lot' and 'lot_id' so UI cards don't raise KeyError."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    row1 = [""] * len(SOLD_ITEMS_HEADERS)
    row1[0] = "SKU-001"
    row1[1] = "LOT-100"
    row1[2] = "Nike Hoodie"
    row1[11] = 45.00
    row1[21] = "ORD-UUID-MATCH-99"
    ws_sold.append_row(row1)

    row2 = [""] * len(SOLD_ITEMS_HEADERS)
    row2[0] = "SKU-002"
    row2[1] = "LOT-100"
    row2[2] = "Nike Hoodie Grey"
    row2[11] = 45.00
    row2[21] = "ORD-UUID-MATCH-99"
    ws_sold.append_row(row2)

    groups = engine.find_duplicate_groups(tab_name=TAB_SOLD_ITEMS)
    assert len(groups) == 1
    rows = groups[0]["rows"]
    assert len(rows) == 2
    for r in rows:
        assert "lot" in r
        assert "lot_id" in r
        assert r["lot"] == "LOT-100"
        assert r["lot_id"] == "LOT-100"


def test_resolve_duplicates_item_listings_preserves_active_status(mock_sheets_env):
    """Ensure resolve_duplicates on Item_Listings merges fields without setting status to Sold or adding Sold_Items formulas."""
    engine, sh = mock_sheets_env
    ws_list = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_list.rows = [list(LISTINGS_HEADERS)]

    # Row 2: Incomplete active listing
    row1 = [""] * len(LISTINGS_HEADERS)
    row1[0] = "SKU-LIST-1"
    row1[1] = ""  # Lot_ID empty
    row1[2] = "Vinted Item"  # Placeholder
    row1[3] = ""  # Brand
    row1[4] = ""  # Category
    row1[6] = 50.00
    row1[7] = 50.00
    row1[13] = "123456789"  # Vinted_ID
    row1[14] = "https://www.vinted.com/items/123456789"
    row1[15] = "Active"
    ws_list.append_row(row1)

    # Row 3: Duplicate with full title, brand, category, and lot
    row2 = [""] * len(LISTINGS_HEADERS)
    row2[0] = "SKU-LIST-2"
    row2[1] = "LOT-888"
    row2[2] = "Levi's 501 Straight Leg Jeans"
    row2[3] = "Levi's"
    row2[4] = "Jeans"
    row2[6] = 50.00
    row2[7] = 50.00
    row2[13] = "123456789"  # Vinted_ID
    row2[14] = "https://www.vinted.com/items/123456789"
    row2[15] = "Active"
    ws_list.append_row(row2)

    assert len(ws_list.rows) == 3

    res = engine.resolve_duplicates(
        tab_name=TAB_ITEM_LISTINGS,
        decisions=[{
            "group_id": "group_1",
            "action": "merge_into",
            "keep_row_idx": 2,
            "discard_row_indices": [3],
        }],
    )

    assert res["deleted_count"] == 1
    assert res["merged_count"] >= 1
    assert len(ws_list.rows) == 2

    kept = ws_list.rows[1]
    assert kept[1] == "LOT-888"
    assert kept[2] == "Levi's 501 Straight Leg Jeans"
    assert kept[3] == "Levi's"
    assert kept[4] == "Jeans"
    # Status MUST remain Active, NOT overwritten with Sold
    assert kept[15] == "Active"


def test_find_duplicate_groups_different_brands_same_title_not_duplicates(mock_sheets_env):
    """When titles are identical but brands are different, items MUST NOT be flagged as duplicates."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Row 2: Nike Graphic T-Shirt
    row1 = [""] * len(SOLD_ITEMS_HEADERS)
    row1[0] = "SKU-NK-1"
    row1[2] = "Vintage Graphic T-Shirt"
    row1[3] = "Nike"
    row1[11] = 25.00
    ws_sold.append_row(row1)

    # Row 3: Adidas Graphic T-Shirt with identical title and price
    row2 = [""] * len(SOLD_ITEMS_HEADERS)
    row2[0] = "SKU-AD-2"
    row2[2] = "Vintage Graphic T-Shirt"
    row2[3] = "Adidas"
    row2[11] = 25.00
    ws_sold.append_row(row2)

    groups = engine.find_duplicate_groups(tab_name=TAB_SOLD_ITEMS)
    # MUST NOT be flagged as duplicates because brands differ!
    assert len(groups) == 0


def test_find_duplicate_groups_same_brand_same_title_is_duplicate(mock_sheets_env):
    """When titles and brands are identical, items MUST be flagged as duplicates."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    # Row 2: Nike Graphic T-Shirt
    row1 = [""] * len(SOLD_ITEMS_HEADERS)
    row1[0] = "SKU-NK-1"
    row1[2] = "Vintage Graphic T-Shirt"
    row1[3] = "Nike"
    row1[11] = 25.00
    ws_sold.append_row(row1)

    # Row 3: Another Nike Graphic T-Shirt with same title and price
    row2 = [""] * len(SOLD_ITEMS_HEADERS)
    row2[0] = "SKU-NK-2"
    row2[2] = "Vintage Graphic T-Shirt"
    row2[3] = "Nike"
    row2[11] = 25.00
    ws_sold.append_row(row2)

    groups = engine.find_duplicate_groups(tab_name=TAB_SOLD_ITEMS)
    assert len(groups) == 1
    assert len(groups[0]["rows"]) == 2


def test_find_matching_sold_row_different_brand_not_matched(mock_sheets_env):
    """find_matching_sold_row MUST NOT match an existing row if both have distinct brands that differ."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)
    ws_sold.rows = [list(SOLD_ITEMS_HEADERS)]

    row1 = [""] * len(SOLD_ITEMS_HEADERS)
    row1[0] = "SKU-NK-99"
    row1[2] = "Heavyweight Fleece Hoodie"
    row1[3] = "Nike"
    row1[11] = 45.00
    row1[14] = "2026-10-20"
    ws_sold.append_row(row1)

    # Matching title and price, but brand is Carhartt
    matched = engine.find_matching_sold_row(
        sold_rows=ws_sold.rows,
        headers=ws_sold.rows[0],
        title="Heavyweight Fleece Hoodie",
        sold_price=45.00,
        sale_date="2026-10-20",
        brand="Carhartt",
    )
    assert matched is None

    # Matching title and price with same brand (Nike) MUST match
    matched_same = engine.find_matching_sold_row(
        sold_rows=ws_sold.rows,
        headers=ws_sold.rows[0],
        title="Heavyweight Fleece Hoodie",
        sold_price=45.00,
        sale_date="2026-10-20",
        brand="Nike",
    )
    assert matched_same == 2


def test_source_tracking_sync_listings_and_orders(mock_sheets_env):
    """Verifies that HTML listings and orders track Source='HTML' in sheets."""
    engine, sh = mock_sheets_env
    ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # 1. New active listing from HTML scraper
    new_item = ItemListing(
        item_sku="",
        title="Arc'teryx Beta LT Jacket",
        brand="Arc'teryx",
        category="Outerwear",
        current_price=220.00,
        status="Listed",
        vinted_id="vinted_999",
        vinted_url="https://www.vinted.com/items/999",
        source="HTML",
    )
    engine.sync_listings([new_item])
    l_headers = ws_listings.rows[0]
    l_src_idx = l_headers.index("Source")
    created_row = [r for r in ws_listings.rows[1:] if "Arc'teryx" in r[2]][0]
    assert created_row[l_src_idx] == "HTML"

    # 2. Moving to sold preserves/tracks source
    moved = engine.move_listing_to_sold(sku=created_row[0], status="Sold", sold_price=210.00)
    assert moved["Source"] == "HTML"
    s_headers = ws_sold.rows[0]
    s_src_idx = s_headers.index("Source")
    assert ws_sold.rows[-1][s_src_idx] == "HTML"


def test_source_tracking_email_and_cross_enrichment(mock_sheets_env):
    """Verifies email sync sets 'Email' and cross-enrichment merges to 'Email + HTML'."""
    from src.email_parser import ParsedEmailEvent

    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # 1. Email event for a brand new item not in active listings
    email_event = ParsedEmailEvent(
        event_type="sale",
        timestamp=datetime(2026, 10, 25, 14, 0, 0),
        vinted_id="778899",
        vinted_url="https://www.vinted.com/items/778899",
        item_title="Stussy Big Ol Jeans",
        sold_price=60.00,
        order_id="ord-stussy-001",
        brand="Stussy",
        category="Jeans",
        source="Email",
    )
    res = engine.process_email_batch([email_event])
    assert res[0]["status"] == "appended_to_sold"

    s_headers = ws_sold.rows[0]
    s_src_idx = s_headers.index("Source")
    appended_row = ws_sold.rows[-1]
    assert appended_row[s_src_idx] == "Email"

    # 2. HTML sync comes in with full order details for the same item -> should enrich and upgrade to 'Email + HTML'
    html_order = SalesOrder(
        order_id="ord-stussy-001",
        item_title="Stussy Big Ol Jeans 32x30",
        brand="Stussy",
        category="Jeans",
        sold_price=60.00,
        sale_date=date(2026, 10, 25),
        source="HTML",
    )
    sync_res = engine.sync_orders([html_order])
    assert sync_res["synced_orders"] == 1
    # Check that source is now 'Email + HTML'
    assert ws_sold.rows[-1][s_src_idx] == "Email + HTML"


def test_reconcile_missing_source_backfill(mock_sheets_env):
    """Verifies that reconcile_missing_sold_metadata automatically backfills blank Source cells."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Row 1: email order with 'ord-' prefix and blank source
    r1 = [""] * len(SOLD_ITEMS_HEADERS)
    r1[0] = "SKU-2001"
    r1[2] = "Nike Fleece Pullover"
    r1[3] = "Nike"
    r1[4] = "Sweaters & Hoodies"
    r1[11] = 35.00
    r1[14] = "2026-10-21"
    r1[21] = "ord-12345"
    r1[25] = ""  # blank source
    ws_sold.append_row(r1)

    # Row 2: html order with uuid and blank source
    r2 = [""] * len(SOLD_ITEMS_HEADERS)
    r2[0] = "SKU-2002"
    r2[2] = "Carhartt Detroit Jacket"
    r2[3] = "Carhartt"
    r2[4] = "Outerwear"
    r2[11] = 85.00
    r2[14] = "2026-10-22"
    r2[21] = "89abcdef-0123-4567-89ab-cdef01234567"
    r2[25] = ""  # blank source
    ws_sold.append_row(r2)

    res = engine.reconcile_missing_sold_metadata()
    assert res["fixed_rows"] >= 2

    # Check backfilled sources
    s_idx = SOLD_ITEMS_HEADERS.index("Source")
    assert ws_sold.rows[-2][s_idx] == "Email"
    assert ws_sold.rows[-1][s_idx] == "HTML"


def test_find_duplicate_groups_includes_source(mock_sheets_env):
    """Verifies that find_duplicate_groups exposes source for each duplicate candidate."""
    engine, sh = mock_sheets_env
    ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

    # Two rows with matching Order_ID: one from Email, one from HTML
    r1 = [""] * len(SOLD_ITEMS_HEADERS)
    r1[0] = "SKU-3001"
    r1[2] = "Order 445566"
    r1[11] = 40.00
    r1[21] = "ord-dup-1"
    r1[25] = "Email"
    ws_sold.append_row(r1)

    r2 = [""] * len(SOLD_ITEMS_HEADERS)
    r2[0] = "SKU-3002"
    r2[2] = "Vintage Polo Ralph Lauren Sweater"
    r2[3] = "Polo Ralph Lauren"
    r2[4] = "Sweaters & Hoodies"
    r2[11] = 40.00
    r2[21] = "ord-dup-1"
    r2[25] = "HTML"
    ws_sold.append_row(r2)

    groups = engine.find_duplicate_groups(TAB_SOLD_ITEMS)
    assert len(groups) == 1
    rows = groups[0]["rows"]
    assert len(rows) == 2
    sources = {r["source"] for r in rows}
    assert sources == {"Email", "HTML"}










