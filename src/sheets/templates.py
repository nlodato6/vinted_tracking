"""Provisions and formats all Google Sheets tabs with schemas, formulas, and dashboards."""

import gspread
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import (
    TAB_INVENTORY_LOTS,
    TAB_ITEM_LISTINGS,
    TAB_SALES_LOG,
    TAB_SHIPPING_SUPPLIES,
    TAB_ANALYTICS_DASHBOARD,
    LOTS_HEADERS,
    LISTINGS_HEADERS,
    SALES_HEADERS,
    SUPPLIES_HEADERS,
)


def format_header_row(worksheet: gspread.Worksheet, num_cols: int):
    """Formats row 1 with dark header background, white text, bold, and freezes the row."""
    worksheet.freeze(rows=1)
    col_letter = chr(ord('A') + num_cols - 1)
    worksheet.format(
        f"A1:{col_letter}1",
        {
            "backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.23},  # #1e293b dark slate
            "textFormat": {
                "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                "bold": True,
                "fontSize": 10,
            },
            "horizontalAlignment": "CENTER",
        },
    )


def initialize_all_tabs(client: GoogleSheetsClient) -> gspread.Spreadsheet:
    """Initializes and formats all 5 tabs in the Google Spreadsheet."""
    sh = client.get_spreadsheet()
    existing_worksheets = {ws.title: ws for ws in sh.worksheets()}

    # 1. Inventory_Lots
    if TAB_INVENTORY_LOTS not in existing_worksheets:
        ws_lots = sh.add_worksheet(title=TAB_INVENTORY_LOTS, rows=200, cols=len(LOTS_HEADERS))
    else:
        ws_lots = existing_worksheets[TAB_INVENTORY_LOTS]
    ws_lots.update("A1", [LOTS_HEADERS])
    format_header_row(ws_lots, len(LOTS_HEADERS))

    # Add sample lot row with formula if empty
    if len(ws_lots.col_values(1)) <= 1:
        sample_lot = [
            "LOT-001",
            "2026-10-15",
            "Goodwill Blue Hanger",
            120.00,
            24,
            "=IF(E2>0, D2/E2, 0)",
            '=COUNTIF(Item_Listings!B:B, A2)',
            "Active",
            "Initial fall clothing haul",
        ]
        ws_lots.append_row(sample_lot, value_input_option="USER_ENTERED")

    # 2. Item_Listings
    if TAB_ITEM_LISTINGS not in existing_worksheets:
        ws_listings = sh.add_worksheet(title=TAB_ITEM_LISTINGS, rows=500, cols=len(LISTINGS_HEADERS))
    else:
        ws_listings = existing_worksheets[TAB_ITEM_LISTINGS]
    ws_listings.update("A1", [LISTINGS_HEADERS])
    format_header_row(ws_listings, len(LISTINGS_HEADERS))

    # Add sample item row if empty
    if len(ws_listings.col_values(1)) <= 1:
        sample_item = [
            "SKU-1001",
            "LOT-001",
            "Vintage Carhartt Canvas Jacket",
            "Carhartt",
            "Outerwear",
            '=IFERROR(VLOOKUP(B2, Inventory_Lots!A:F, 6, FALSE), 0)',
            65.00,
            60.00,
            14,
            85,
            "sample_id_123",
            "https://www.vinted.com/items/sample_id_123",
            "Listed",
            "2026-10-16",
            "2026-10-19 14:00:00",
        ]
        ws_listings.append_row(sample_item, value_input_option="USER_ENTERED")

    # 3. Shipping_Supplies
    if TAB_SHIPPING_SUPPLIES not in existing_worksheets:
        ws_supplies = sh.add_worksheet(title=TAB_SHIPPING_SUPPLIES, rows=200, cols=len(SUPPLIES_HEADERS))
    else:
        ws_supplies = existing_worksheets[TAB_SHIPPING_SUPPLIES]
    ws_supplies.update("A1", [SUPPLIES_HEADERS])
    format_header_row(ws_supplies, len(SUPPLIES_HEADERS))

    # Add sample supply row & global average formula in H2 if empty
    if len(ws_supplies.col_values(1)) <= 1:
        sample_supply = [
            "SUP-01",
            "2026-10-01",
            "10x13 Poly Mailers (Pack of 100)",
            "Amazon",
            100,
            18.99,
            "=IF(E2>0, F2/E2, 0)",
            '=IF(SUM(E2:E)>0, SUM(F2:F)/SUM(E2:E), 0.75)',
        ]
        ws_supplies.append_row(sample_supply, value_input_option="USER_ENTERED")

    # 4. Sales_Log
    if TAB_SALES_LOG not in existing_worksheets:
        ws_sales = sh.add_worksheet(title=TAB_SALES_LOG, rows=500, cols=len(SALES_HEADERS))
    else:
        ws_sales = existing_worksheets[TAB_SALES_LOG]
    ws_sales.update("A1", [SALES_HEADERS])
    format_header_row(ws_sales, len(SALES_HEADERS))

    # Add sample sales row if empty
    if len(ws_sales.col_values(1)) <= 1:
        sample_sale = [
            "ORD-982104",
            "Vinted",
            "SKU-1001",
            "Vintage Carhartt Canvas Jacket",
            "Carhartt",
            "Outerwear",
            14,
            "2026-10-19",
            55.00,
            0.00,
            55.00,
            '=IFERROR(VLOOKUP(C2, Item_Listings!A:F, 6, FALSE), 0)',
            '=IF(B2="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
            '=K2-L2-M2',
            '=IF(I2>0, N2/I2, 0)',
            "Completed",
        ]
        ws_sales.append_row(sample_sale, value_input_option="USER_ENTERED")

    # 5. Analytics_Dashboard
    if TAB_ANALYTICS_DASHBOARD not in existing_worksheets:
        ws_dash = sh.add_worksheet(title=TAB_ANALYTICS_DASHBOARD, rows=65, cols=10)
    else:
        ws_dash = existing_worksheets[TAB_ANALYTICS_DASHBOARD]

    # Populate Dashboard Layout with Financials, Favorites Analytics, Categories & Platforms
    dashboard_layout = [
        # Row 1-2: Title
        ["📊 OMNICHANNEL RESELLING DASHBOARD (VINTED & FB MARKETPLACE)", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],

        # Section 1: Financial Performance (Rows 3-10)
        ["💵 FINANCIAL PERFORMANCE", "", "", "📦 INVENTORY & EXPENSES", "", "", "", ""],
        ["Metric", "Value", "Notes", "Metric", "Value", "Notes", "", ""],
        ["Gross Sales Revenue", "=IFERROR(SUM(Sales_Log!I2:I), 0)", "Gross buyer spend across all channels", "Total Inventory Invested", "=IFERROR(SUM(Inventory_Lots!D2:D), 0)", "Total spent on hauls", "", ""],
        ["Net Payout Revenue", "=IFERROR(SUM(Sales_Log!K2:K), 0)", "Actual balance received", "Total Supplies Invested", "=IFERROR(SUM(Shipping_Supplies!F2:F), 0)", "Packaging & supplies spend", "", ""],
        ["Cost of Goods Sold (COGS)", "=IFERROR(SUM(Sales_Log!L2:L), 0)", "Allocated lot cost of sold items", "Active Listed Inventory Value", '=IFERROR(SUMIF(Item_Listings!M:M, "Listed", Item_Listings!H:H), 0)', "Current asking price sum", "", ""],
        ["Packaging Expenses Allocated", "=IFERROR(SUM(Sales_Log!M2:M), 0)", "Supplies deducted per sale", "Active Articles in Stock", '=IFERROR(COUNTIF(Item_Listings!M:M, "Listed"), 0)', "Garments currently for sale", "", ""],
        ["Total Net Profit Realized", "=IFERROR(SUM(Sales_Log!N2:N), 0)", "Net Payout - COGS - Packaging", "Total Articles Sold", '=IFERROR(COUNTA(Sales_Log!A2:A), 0)', "Garments successfully sold", "", ""],
        ["Overall Net Margin %", '=IF(B5>0, B9/B5, 0)', "Net Profit ÷ Gross Sales", "Avg Supply Cost / Package", '=IFERROR(Shipping_Supplies!H2, 0.75)', "Dynamic per-package cost", "", ""],
        ["", "", "", "", "", "", "", ""],

        # Section 2: Favorites & Conversion Analytics (Rows 12-18)
        ["❤️ FAVORITES & BUYER INTENT CONVERSION", "", "", "", "", "", "", ""],
        ["Metric", "Value", "Benchmark / Interpretation", "", "", "", "", ""],
        ["Avg Favorites on Sold Items", "=IFERROR(AVERAGE(Sales_Log!G2:G), 0)", "Average likes an item accumulates before selling", "", "", "", "", ""],
        ["Avg Favorites on Active Items", '=IFERROR(AVERAGEIF(Item_Listings!M:M, "Listed", Item_Listings!I:I), 0)', "Average likes across current closet inventory", "", "", "", "", ""],
        ["High-Interest Active Garments (≥5 Favs)", '=IFERROR(COUNTIFS(Item_Listings!M:M, "Listed", Item_Listings!I:I, ">=5"), 0)', "Hot items ready to sell; send discount offers!", "", "", "", "", ""],
        ["Favorited Item Conversion Rate %", '=IFERROR(COUNTIF(Sales_Log!G2:G, ">=1") / (COUNTIF(Sales_Log!G2:G, ">=1") + COUNTIFS(Item_Listings!M:M, "Listed", Item_Listings!I:I, ">=1")), 0)', "Proportion of favorited items that resulted in a sale", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],

        # Section 3: Clothing Category Performance Leaderboard (Rows 20-30)
        ["👔 CLOTHING CATEGORY PERFORMANCE: WHAT SELLS BEST & FOR THE MOST MONEY", "", "", "", "", "", "", ""],
        ["Category", "Total Listed", "Units Sold", "Sell-Through %", "Avg Sale Price ($)", "Total Revenue ($)", "Avg Net Profit ($)", "Avg Favorites"],
        ["Outerwear", '=COUNTIF(Item_Listings!E:E, A22)', '=COUNTIF(Sales_Log!F:F, A22)', '=IF(B22>0, C22/B22, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A22, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A22, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A22, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A22, Sales_Log!G:G), 0)'],
        ["Sweaters & Hoodies", '=COUNTIF(Item_Listings!E:E, A23)', '=COUNTIF(Sales_Log!F:F, A23)', '=IF(B23>0, C23/B23, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A23, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A23, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A23, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A23, Sales_Log!G:G), 0)'],
        ["Tops & Shirts", '=COUNTIF(Item_Listings!E:E, A24)', '=COUNTIF(Sales_Log!F:F, A24)', '=IF(B24>0, C24/B24, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A24, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A24, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A24, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A24, Sales_Log!G:G), 0)'],
        ["Pants & Jeans", '=COUNTIF(Item_Listings!E:E, A25)', '=COUNTIF(Sales_Log!F:F, A25)', '=IF(B25>0, C25/B25, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A25, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A25, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A25, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A25, Sales_Log!G:G), 0)'],
        ["Footwear", '=COUNTIF(Item_Listings!E:E, A26)', '=COUNTIF(Sales_Log!F:F, A26)', '=IF(B26>0, C26/B26, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A26, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A26, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A26, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A26, Sales_Log!G:G), 0)'],
        ["Accessories", '=COUNTIF(Item_Listings!E:E, A27)', '=COUNTIF(Sales_Log!F:F, A27)', '=IF(B27>0, C27/B27, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A27, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A27, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A27, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A27, Sales_Log!G:G), 0)'],
        ["Dresses & Skirts", '=COUNTIF(Item_Listings!E:E, A28)', '=COUNTIF(Sales_Log!F:F, A28)', '=IF(B28>0, C28/B28, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A28, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A28, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A28, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A28, Sales_Log!G:G), 0)'],
        ["Other Apparel", '=COUNTIF(Item_Listings!E:E, A29)', '=COUNTIF(Sales_Log!F:F, A29)', '=IF(B29>0, C29/B29, 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A29, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!F:F, A29, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A29, Sales_Log!N:N), 0)', '=IFERROR(AVERAGEIF(Sales_Log!F:F, A29, Sales_Log!G:G), 0)'],
        ["", "", "", "", "", "", "", ""],

        # Section 4: Brand Performance Leaderboard (Rows 32-41)
        ["🏷️ TOP BRAND LEADERBOARD (BY REVENUE & MARGIN)", "", "", "", "", "", "", ""],
        ["Brand", "Units Sold", "Avg Sale Price ($)", "Total Revenue ($)", "Avg Net Profit ($)", "", "", ""],
        ["Carhartt", '=COUNTIF(Sales_Log!E:E, A34)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A34, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A34, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A34, Sales_Log!N:N), 0)', "", "", ""],
        ["Nike", '=COUNTIF(Sales_Log!E:E, A35)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A35, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A35, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A35, Sales_Log!N:N), 0)', "", "", ""],
        ["Levi's", '=COUNTIF(Sales_Log!E:E, A36)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A36, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A36, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A36, Sales_Log!N:N), 0)', "", "", ""],
        ["The North Face", '=COUNTIF(Sales_Log!E:E, A37)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A37, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A37, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A37, Sales_Log!N:N), 0)', "", "", ""],
        ["Patagonia", '=COUNTIF(Sales_Log!E:E, A38)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A38, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A38, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A38, Sales_Log!N:N), 0)', "", "", ""],
        ["Vintage", '=COUNTIF(Sales_Log!E:E, A39)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A39, Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!E:E, A39, Sales_Log!I:I), 0)', '=IFERROR(AVERAGEIF(Sales_Log!E:E, A39, Sales_Log!N:N), 0)', "", "", ""],
        ["", "", "", "", "", "", "", ""],

        # Section 5: Platform Breakdown (Rows 41-47)
        ["🌐 SALES BREAKDOWN BY PLATFORM (VINTED VS FB MARKETPLACE)", "", "", "", "", "", "", ""],
        ["Platform Channel", "Units Sold", "Gross Revenue ($)", "Total Net Profit ($)", "Avg Profit / Item", "", "", ""],
        ["Vinted", '=COUNTIF(Sales_Log!B:B, "Vinted")', '=IFERROR(SUMIF(Sales_Log!B:B, "Vinted", Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!B:B, "Vinted", Sales_Log!N:N), 0)', '=IF(B43>0, D43/B43, 0)', "", "", ""],
        ["FB Marketplace", '=COUNTIF(Sales_Log!B:B, "FB Marketplace*")', '=IFERROR(SUMIF(Sales_Log!B:B, "FB Marketplace*", Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!B:B, "FB Marketplace*", Sales_Log!N:N), 0)', '=IF(B44>0, D44/B44, 0)', "", "", ""],
        ["Other Channels", '=COUNTIF(Sales_Log!B:B, "Other*")', '=IFERROR(SUMIF(Sales_Log!B:B, "Other*", Sales_Log!I:I), 0)', '=IFERROR(SUMIF(Sales_Log!B:B, "Other*", Sales_Log!N:N), 0)', '=IF(B45>0, D45/B45, 0)', "", "", ""],
    ]
    ws_dash.update("A1", dashboard_layout, value_input_option="USER_ENTERED")

    # Format Main Dashboard Header
    ws_dash.format(
        "A1:H1",
        {
            "backgroundColor": {"red": 0.08, "green": 0.44, "blue": 0.65},  # Teal blue
            "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 13},
            "horizontalAlignment": "CENTER",
        }
    )

    # Section Headers
    section_ranges = ["A3:F4", "A12:C13", "A20:H21", "A32:E33", "A41:E42"]
    for s_range in section_ranges:
        ws_dash.format(
            s_range,
            {
                "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.32},
                "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER",
            }
        )

    # Format numeric/currency columns
    ws_dash.format("E22:G29", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})
    ws_dash.format("C34:E39", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})
    ws_dash.format("C43:E45", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})
    ws_dash.format("D22:D29", {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}})
    ws_dash.format("B10", {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}})
    ws_dash.format("B17", {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}})

    # Remove default "Sheet1" if it exists
    if "Sheet1" in existing_worksheets and len(sh.worksheets()) > 1:
        try:
            sh.del_worksheet(existing_worksheets["Sheet1"])
        except Exception:
            pass

    return sh
