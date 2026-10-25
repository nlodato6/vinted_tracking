"""Unit tests for Hidden_Items and Cancelled_Orders schemas and template initialization."""

from unittest.mock import MagicMock
import pytest
from src.sheets.schemas import (
    TAB_INVENTORY_LOTS,
    TAB_ITEM_LISTINGS,
    TAB_HIDDEN_ITEMS,
    TAB_SOLD_ITEMS,
    TAB_CANCELLED_ORDERS,
    TAB_SHIPPING_SUPPLIES,
    TAB_ANALYTICS_DASHBOARD,
    SHEET_TABS,
    LISTINGS_HEADERS,
    HIDDEN_ITEMS_HEADERS,
    SOLD_ITEMS_HEADERS,
    CANCELLED_ORDERS_HEADERS,
)
from src.sheets.templates import initialize_all_tabs


def test_schemas_definitions():
    """Verifies that tab constants and headers for Hidden and Cancelled items are properly registered."""
    assert TAB_HIDDEN_ITEMS == "Hidden_Items"
    assert TAB_CANCELLED_ORDERS == "Cancelled_Orders"

    assert TAB_HIDDEN_ITEMS in SHEET_TABS
    assert TAB_CANCELLED_ORDERS in SHEET_TABS

    # Hidden items should match Item_Listings schema
    assert HIDDEN_ITEMS_HEADERS == LISTINGS_HEADERS
    assert "Item_SKU" in HIDDEN_ITEMS_HEADERS
    assert "Status" in HIDDEN_ITEMS_HEADERS
    assert "Source" in HIDDEN_ITEMS_HEADERS

    # Cancelled orders headers
    expected_cancelled_cols = [
        "Order_ID",
        "Item_SKU",
        "Item_Title",
        "Brand",
        "Category",
        "Lost_Revenue",
        "Original_Price",
        "Cancellation_Date",
        "Status_Details",
        "Buyer_Username",
        "Vinted_URL",
        "Source",
    ]
    assert CANCELLED_ORDERS_HEADERS == expected_cancelled_cols


def test_initialize_all_tabs_provisions_hidden_and_cancelled():
    """Verifies initialize_all_tabs creates and formats Hidden_Items and Cancelled_Orders worksheets."""
    mock_sh = MagicMock()
    created_worksheets = {}

    def mock_add_worksheet(title, rows, cols):
        ws = MagicMock()
        ws.title = title
        ws.row_count = rows
        ws.col_count = cols
        created_worksheets[title] = ws
        return ws

    mock_sh.worksheets.return_value = []
    mock_sh.add_worksheet.side_effect = mock_add_worksheet

    mock_client = MagicMock()
    mock_client.get_spreadsheet.return_value = mock_sh

    sh = initialize_all_tabs(mock_client)

    # Check that both new tabs were provisioned
    assert TAB_HIDDEN_ITEMS in created_worksheets
    assert TAB_CANCELLED_ORDERS in created_worksheets

    ws_hidden = created_worksheets[TAB_HIDDEN_ITEMS]
    ws_cancelled = created_worksheets[TAB_CANCELLED_ORDERS]

    # Verify header rows were updated
    hidden_updates = [call for call in ws_hidden.update.call_args_list if call.kwargs.get("range_name") == "A1"]
    assert len(hidden_updates) == 1
    assert hidden_updates[0].kwargs["values"] == [HIDDEN_ITEMS_HEADERS]

    cancelled_updates = [call for call in ws_cancelled.update.call_args_list if call.kwargs.get("range_name") == "A1"]
    assert len(cancelled_updates) == 1
    assert cancelled_updates[0].kwargs["values"] == [CANCELLED_ORDERS_HEADERS]

    # Verify formatting calls were executed on both
    assert ws_hidden.format.called
    assert ws_cancelled.format.called


def test_get_worksheet_auto_provisions_cancelled_orders():
    """Verifies that SheetsSyncEngine.get_worksheet automatically creates Cancelled_Orders if missing."""
    from src.sheets.sync import SheetsSyncEngine
    mock_sh = MagicMock()
    mock_client = MagicMock()
    mock_client.get_spreadsheet.return_value = mock_sh

    # Simulate WorksheetNotFound when first requested
    import gspread
    mock_sh.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("Cancelled_Orders")

    created_worksheets = {}

    def mock_add_ws(title, rows, cols):
        ws = MagicMock()
        ws.title = title
        created_worksheets[title] = ws
        return ws

    mock_sh.add_worksheet.side_effect = mock_add_ws

    engine = SheetsSyncEngine(mock_client)
    ws = engine.get_worksheet(TAB_CANCELLED_ORDERS)

    assert ws is not None
    assert TAB_CANCELLED_ORDERS in created_worksheets
    assert mock_sh.add_worksheet.called
    # Check headers were written
    header_calls = [c for c in ws.update.call_args_list if c.kwargs.get("range_name") == "A1"]
    assert len(header_calls) == 1
    assert header_calls[0].kwargs["values"] == [CANCELLED_ORDERS_HEADERS]


def test_ensure_required_tabs_provisions_all_missing():
    """Verifies ensure_required_tabs discovers missing tabs and provisions them."""
    from src.sheets.sync import SheetsSyncEngine
    mock_sh = MagicMock()
    mock_client = MagicMock()
    mock_client.get_spreadsheet.return_value = mock_sh

    # Only Item_Listings and Sold_Items exist initially
    ws_listings = MagicMock(title=TAB_ITEM_LISTINGS)
    ws_sold = MagicMock(title=TAB_SOLD_ITEMS)
    mock_sh.worksheets.return_value = [ws_listings, ws_sold]

    # Map existing vs missing on worksheet() call
    def mock_worksheet(title):
        if title == TAB_ITEM_LISTINGS:
            return ws_listings
        if title == TAB_SOLD_ITEMS:
            return ws_sold
        raise Exception(f"Worksheet {title} not found")

    mock_sh.worksheet.side_effect = mock_worksheet

    created_worksheets = {}

    def mock_add_ws(title, rows, cols):
        ws = MagicMock(title=title)
        created_worksheets[title] = ws
        return ws

    mock_sh.add_worksheet.side_effect = mock_add_ws

    engine = SheetsSyncEngine(mock_client)
    created = engine.ensure_required_tabs()

    assert TAB_CANCELLED_ORDERS in created
    assert TAB_HIDDEN_ITEMS in created
    assert TAB_INVENTORY_LOTS in created
    assert TAB_SHIPPING_SUPPLIES in created
    assert len(created_worksheets) == 4
