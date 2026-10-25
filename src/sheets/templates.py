"""Provisions and formats all Google Sheets tabs with schemas, formulas, and dashboards."""

import gspread
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import (
    TAB_INVENTORY_LOTS,
    TAB_ITEM_LISTINGS,
    TAB_HIDDEN_ITEMS,
    TAB_SOLD_ITEMS,
    TAB_CANCELLED_ORDERS,
    TAB_SALES_LOG,
    TAB_SHIPPING_SUPPLIES,
    TAB_ANALYTICS_DASHBOARD,
    LOTS_HEADERS,
    LISTINGS_HEADERS,
    HIDDEN_ITEMS_HEADERS,
    SOLD_ITEMS_HEADERS,
    CANCELLED_ORDERS_HEADERS,
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


def ensure_col_count(worksheet: gspread.Worksheet, min_cols: int):
    """Ensures worksheet has at least min_cols columns, expanding if necessary."""
    if worksheet.col_count < min_cols:
        worksheet.add_cols(min_cols - worksheet.col_count)


def safe_batch_format(worksheet: gspread.Worksheet, formats: list):
    """Executes multiple formatting requests in a single batch API call to conserve rate limits."""
    if not formats:
        return
    try:
        worksheet.batch_format(formats)
    except Exception:
        for f in formats:
            try:
                worksheet.format(f["range"], f["format"])
            except Exception:
                pass


def initialize_all_tabs(client: GoogleSheetsClient) -> gspread.Spreadsheet:
    """Initializes and formats all tabs in the Google Spreadsheet."""
    sh = client.get_spreadsheet()
    existing_worksheets = {ws.title: ws for ws in sh.worksheets()}

    # 1. Inventory_Lots
    if TAB_INVENTORY_LOTS not in existing_worksheets:
        ws_lots = sh.add_worksheet(title=TAB_INVENTORY_LOTS, rows=200, cols=len(LOTS_HEADERS))
    else:
        ws_lots = existing_worksheets[TAB_INVENTORY_LOTS]
    ensure_col_count(ws_lots, len(LOTS_HEADERS))
    ws_lots.update(range_name="A1", values=[LOTS_HEADERS])
    format_header_row(ws_lots, len(LOTS_HEADERS))

    # Pre-populate automated formulas for Unit_Cost, Items_Processed, and Status (rows 2 to 100)
    lots_formula_rows = []
    for r in range(2, 101):
        f_cost = f'=IF(A{r}="", "", IF(AND(ISNUMBER(E{r}), E{r}>0), D{r}/E{r}, ""))'
        f_proc = f'=IF(A{r}="", "", COUNTIF(Item_Listings!B:B, A{r}) + COUNTIF(Sold_Items!B:B, A{r}))'
        f_status = (
            f'=IF(A{r}="", "", IF(AND(ISNUMBER(E{r}), E{r}>0, COUNTIF(Sold_Items!B:B, A{r})>=E{r}), '
            f'"Completed", IF(COUNTIF(Item_Listings!B:B, A{r})+COUNTIF(Sold_Items!B:B, A{r})>0, '
            f'"Active", "Pending")))'
        )
        lots_formula_rows.append([f_cost, f_proc, f_status])
    ws_lots.update(range_name="F2:H100", values=lots_formula_rows, value_input_option="USER_ENTERED")

    # Format numeric and currency columns in Inventory_Lots
    ws_lots.format("D2:D100", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})
    ws_lots.format("E2:E100", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
    ws_lots.format("F2:F100", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})
    ws_lots.format("G2:G100", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
    ws_lots.format("H2:H100", {"horizontalAlignment": "CENTER"})

    # 2. Item_Listings (Active listings only)
    if TAB_ITEM_LISTINGS not in existing_worksheets:
        ws_listings = sh.add_worksheet(title=TAB_ITEM_LISTINGS, rows=500, cols=len(LISTINGS_HEADERS))
    else:
        ws_listings = existing_worksheets[TAB_ITEM_LISTINGS]
    ensure_col_count(ws_listings, len(LISTINGS_HEADERS))
    ws_listings.update(range_name="A1", values=[LISTINGS_HEADERS])
    format_header_row(ws_listings, len(LISTINGS_HEADERS))

    # 3. Hidden_Items (Temporarily hidden listings, off-market/seasonal)
    if TAB_HIDDEN_ITEMS not in existing_worksheets:
        ws_hidden = sh.add_worksheet(title=TAB_HIDDEN_ITEMS, rows=500, cols=len(HIDDEN_ITEMS_HEADERS))
    else:
        ws_hidden = existing_worksheets[TAB_HIDDEN_ITEMS]
    ensure_col_count(ws_hidden, len(HIDDEN_ITEMS_HEADERS))
    ws_hidden.update(range_name="A1", values=[HIDDEN_ITEMS_HEADERS])
    format_header_row(ws_hidden, len(HIDDEN_ITEMS_HEADERS))

    # 4. Sold_Items (Consolidated sales ledger and sold archive)
    if TAB_SOLD_ITEMS not in existing_worksheets:
        ws_sold = sh.add_worksheet(title=TAB_SOLD_ITEMS, rows=500, cols=len(SOLD_ITEMS_HEADERS))
    else:
        ws_sold = existing_worksheets[TAB_SOLD_ITEMS]
    ensure_col_count(ws_sold, len(SOLD_ITEMS_HEADERS))
    ws_sold.update(range_name="A1", values=[SOLD_ITEMS_HEADERS])
    format_header_row(ws_sold, len(SOLD_ITEMS_HEADERS))

    # 5. Cancelled_Orders (Aborted, failed, or refunded transactions)
    if TAB_CANCELLED_ORDERS not in existing_worksheets:
        ws_cancelled = sh.add_worksheet(title=TAB_CANCELLED_ORDERS, rows=300, cols=len(CANCELLED_ORDERS_HEADERS))
    else:
        ws_cancelled = existing_worksheets[TAB_CANCELLED_ORDERS]
    ensure_col_count(ws_cancelled, len(CANCELLED_ORDERS_HEADERS))
    ws_cancelled.update(range_name="A1", values=[CANCELLED_ORDERS_HEADERS])
    format_header_row(ws_cancelled, len(CANCELLED_ORDERS_HEADERS))

    # Format Item_Listings
    safe_batch_format(ws_listings, [
        {"range": "F2:H500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "I2:I500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "J2:J500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "L2:M500", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "P2:P500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "S2:S500", "format": {"horizontalAlignment": "CENTER"}},
    ])

    # Format Hidden_Items
    safe_batch_format(ws_hidden, [
        {"range": "F2:H500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "I2:I500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "J2:J500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "L2:M500", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "P2:P500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "S2:S500", "format": {"horizontalAlignment": "CENTER"}},
    ])

    # Format Sold_Items
    safe_batch_format(ws_sold, [
        {"range": "F2:H500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "I2:I500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "J2:J500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "L2:M500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "N2:P500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "Q2:R500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "S2:S500", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        {"range": "T2:U500", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "X2:X500", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "Z2:Z500", "format": {"horizontalAlignment": "CENTER"}},
    ])

    # Format Cancelled_Orders
    safe_batch_format(ws_cancelled, [
        {"range": "F2:G300", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "H2:H300", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "I2:I300", "format": {"horizontalAlignment": "CENTER"}},
        {"range": "L2:L300", "format": {"horizontalAlignment": "CENTER"}},
    ])

    # 4. Shipping_Supplies
    if TAB_SHIPPING_SUPPLIES not in existing_worksheets:
        ws_supplies = sh.add_worksheet(title=TAB_SHIPPING_SUPPLIES, rows=200, cols=len(SUPPLIES_HEADERS))
    else:
        ws_supplies = existing_worksheets[TAB_SHIPPING_SUPPLIES]
    ensure_col_count(ws_supplies, len(SUPPLIES_HEADERS))
    ws_supplies.update(range_name="A1", values=[SUPPLIES_HEADERS])
    format_header_row(ws_supplies, len(SUPPLIES_HEADERS))

    # Ensure unit cost and global average packaging formulas across rows 2 to 100
    supplies_formula_rows = []
    for r in range(2, 101):
        f_unit = f"=IF(AND(ISNUMBER(E{r}), E{r}>0), F{r}/E{r}, \"\")"
        f_avg = "=IF(SUM(E2:E)>0, SUM(F2:F)/SUM(E2:E), 0.25)" if r == 2 else f"=IF(E{r}=\"\", \"\", H$2)"
        supplies_formula_rows.append([f_unit, f_avg])
    ws_supplies.update(range_name="G2:H100", values=supplies_formula_rows, value_input_option="USER_ENTERED")

    all_supplies_rows = ws_supplies.get_all_values()
    if len(all_supplies_rows) <= 1:
        sample_supply = [
            "SUP-01",
            "2026-10-01",
            "10x13 Poly Mailers (Pack of 100)",
            "Amazon",
            100,
            18.99,
        ]
        ws_supplies.append_row(sample_supply, value_input_option="USER_ENTERED")

    ws_supplies.format("E2:E200", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
    ws_supplies.format("F2:H200", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})

    # Cleanly retire old redundant Sales_Log if present
    if TAB_SALES_LOG in existing_worksheets:
        try:
            sh.del_worksheet(existing_worksheets[TAB_SALES_LOG])
        except Exception:
            pass

    # 5. Analytics_Dashboard
    if TAB_ANALYTICS_DASHBOARD not in existing_worksheets:
        ws_dash = sh.add_worksheet(title=TAB_ANALYTICS_DASHBOARD, rows=70, cols=10)
    else:
        ws_dash = existing_worksheets[TAB_ANALYTICS_DASHBOARD]

    # Populate Dashboard Layout with Financials, Price Reduction & Offer Analytics, Categories & Platforms
    dashboard_layout = [
        # Row 1-2: Title & System Banner
        ["📊 OMNICHANNEL RESELLING DASHBOARD (VINTED & FB MARKETPLACE)", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 1: Financial Performance (Rows 3-11)
        ["💵 FINANCIAL PERFORMANCE", "", "", "📦 INVENTORY & EXPENSES", "", "", "", "", ""],
        ["Metric", "Value", "Notes", "Metric", "Value", "Notes", "", "", ""],
        ["Gross Sales Revenue", "=IFERROR(SUM(Sold_Items!L2:L), 0)", "Gross buyer spend across all channels", "Total Inventory Invested", "=IFERROR(SUM(Inventory_Lots!D2:D), 0)", "Total spent on bulk hauls", "", "", ""],
        ["Net Payout Revenue", "=IFERROR(SUM(Sold_Items!L2:L), 0)", "Zero Vinted seller fees ($0)", "Total Supplies Invested", "=IFERROR(SUM(Shipping_Supplies!F2:F), 0)", "Packaging & supplies spend", "", "", ""],
        ["Cost of Goods Sold (COGS)", "=IFERROR(SUM(Sold_Items!F2:F), 0)", "Allocated lot cost of sold items", "Active Listed Inventory Value", '=IFERROR(SUM(Item_Listings!H2:H), 0)', "Current asking price sum of active items", "", "", ""],
        ["Packaging Expenses Allocated", "=IFERROR(SUM(Sold_Items!Q2:Q), 0)", "Supplies deducted per sale", "Active Articles in Stock", '=IFERROR(COUNTA(Item_Listings!A2:A), 0)', "Garments currently for sale", "", "", ""],
        ["Total Net Profit Realized", "=IFERROR(SUM(Sold_Items!R2:R), 0)", "Gross Payout - COGS - Packaging", "Total Articles Sold", '=IFERROR(COUNTA(Sold_Items!A2:A), 0)', "Garments successfully sold", "", "", ""],
        ["Overall Net Margin %", '=IF(B5>0, B9/B5, 0)', "Net Profit ÷ Gross Sales", "Avg Supply Cost / Package", '=IFERROR(Shipping_Supplies!H2, 0.24)', "Dynamic per-package cost", "", "", ""],
        ["Actual Net Cash Profit", '=IFERROR(B6-E5-E6, 0)', "Actual cash in pocket: Net Payout ($) - Total Inventory Invested ($) - Total Supplies Invested ($)", "Unsold Inventory Cost Basis", '=IFERROR(SUM(Item_Listings!F2:F), 0)', "Acquisition cost of unsold closet articles (active stock)", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 2: Price Reduction & Offer Acceptance Analytics (Rows 13-19)
        ["🏷️ PRICE REDUCTION & OFFER ACCEPTANCE ANALYTICS", "", "", "📉 PRICE DROP & DISCOUNT BENCHMARKS", "", "", "", "", ""],
        ["Sale Price Type", "Units Sold", "% of Sold Units", "Discount Metric", "Value", "Interpretation / Notes", "", "", ""],
        ["Original Posted Price (Full Asking)", '=IFERROR(COUNTIF(Sold_Items!N:N, "Original Price"), 0)', '=IF(E9>0, B15/E9, 0)', "Avg Price Drop on Discounted Sales ($)", '=IFERROR(AVERAGEIF(Sold_Items!M:M, ">0"), 0)', "Average dollar markdown before sale", "", "", ""],
        ["Lowered Listed Price (Markdowns)", '=IFERROR(COUNTIF(Sold_Items!N:N, "Lowered Price"), 0)', '=IF(E9>0, B16/E9, 0)', "Avg % Markdown on Discounted Sales", '=IFERROR(AVERAGEIF(Sold_Items!M:M, ">0", Sold_Items!M:M)/AVERAGEIF(Sold_Items!M:M, ">0", Sold_Items!G:G), 0)', "Average percentage drop before sale", "", "", ""],
        ["Accepted Offer (Below Listed)", '=IFERROR(COUNTIF(Sold_Items!N:N, "Accepted Offer"), 0)', '=IF(E9>0, B17/E9, 0)', "Overall Sales Discount Rate %", '=IF(SUM(Sold_Items!G:G)>0, SUM(Sold_Items!M:M)/SUM(Sold_Items!G:G), 0)', "Total dollars discounted ÷ total original asking prices", "", "", ""],
        ["Total Sold Units", '=SUM(B15:B17)', '=IF(E9>0, B18/E9, 0)', "Active Items Currently Lowered", '=COUNTIF(Item_Listings!I:I, "YES")', "Active closet items with live price cuts", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 3: Favorites & Buyer Intent Conversion (Rows 20-26)
        ["❤️ FAVORITES & BUYER INTENT CONVERSION", "", "", "", "", "", "", "", ""],
        ["Metric", "Value", "Benchmark / Interpretation", "", "", "", "", "", ""],
        ["Avg Favorites on Sold Items", "=IFERROR(AVERAGE(Sold_Items!T2:T), 0)", "Average likes an item accumulates before selling", "", "", "", "", "", ""],
        ["Avg Favorites on Active Items", '=IFERROR(AVERAGE(Item_Listings!L2:L), 0)', "Average likes across current closet inventory", "", "", "", "", "", ""],
        ["High-Interest Active Garments (≥5 Favs)", '=IFERROR(COUNTIF(Item_Listings!L:L, ">=5"), 0)', "Hot items ready to sell; send discount offers!", "", "", "", "", "", ""],
        ["Favorited Item Conversion Rate %", '=IFERROR(COUNTIF(Sold_Items!T2:T, ">=1") / (COUNTIF(Sold_Items!T2:T, ">=1") + COUNTIF(Item_Listings!L2:L, ">=1")), 0)', "Proportion of favorited items that resulted in a sale", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 4: Clothing Category Performance & Markdowns Leaderboard (Rows 27-37)
        ["👔 CLOTHING CATEGORY PERFORMANCE: WHAT SELLS BEST & GETS LOWERED THE MOST", "", "", "", "", "", "", "", ""],
        ["Category", "Total Listed", "Units Sold", "Sell-Through %", "Avg Sale Price ($)", "Total Revenue ($)", "Avg Net Profit ($)", "Avg Price Drop ($)", "% Sold Lowered"],
        ["Outerwear", '=COUNTIF(Item_Listings!E:E, A29) + COUNTIF(Sold_Items!E:E, A29)', '=COUNTIF(Sold_Items!E:E, A29)', '=IF(B29>0, C29/B29, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A29, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A29, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A29, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A29, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A29, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A29), 0)'],
        ["Sweaters & Hoodies", '=COUNTIF(Item_Listings!E:E, A30) + COUNTIF(Sold_Items!E:E, A30)', '=COUNTIF(Sold_Items!E:E, A30)', '=IF(B30>0, C30/B30, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A30, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A30, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A30, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A30, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A30, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A30), 0)'],
        ["Tops & Shirts", '=COUNTIF(Item_Listings!E:E, A31) + COUNTIF(Sold_Items!E:E, A31)', '=COUNTIF(Sold_Items!E:E, A31)', '=IF(B31>0, C31/B31, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A31, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A31, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A31, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A31, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A31, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A31), 0)'],
        ["Pants & Jeans", '=COUNTIF(Item_Listings!E:E, A32) + COUNTIF(Sold_Items!E:E, A32)', '=COUNTIF(Sold_Items!E:E, A32)', '=IF(B32>0, C32/B32, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A32, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A32, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A32, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A32, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A32, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A32), 0)'],
        ["Footwear", '=COUNTIF(Item_Listings!E:E, A33) + COUNTIF(Sold_Items!E:E, A33)', '=COUNTIF(Sold_Items!E:E, A33)', '=IF(B33>0, C33/B33, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A33, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A33, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A33, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A33, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A33, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A33), 0)'],
        ["Accessories", '=COUNTIF(Item_Listings!E:E, A34) + COUNTIF(Sold_Items!E:E, A34)', '=COUNTIF(Sold_Items!E:E, A34)', '=IF(B34>0, C34/B34, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A34, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A34, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A34, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A34, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A34, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A34), 0)'],
        ["Dresses & Skirts", '=COUNTIF(Item_Listings!E:E, A35) + COUNTIF(Sold_Items!E:E, A35)', '=COUNTIF(Sold_Items!E:E, A35)', '=IF(B35>0, C35/B35, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A35, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A35, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A35, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A35, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A35, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A35), 0)'],
        ["Other Apparel", '=COUNTIF(Item_Listings!E:E, A36) + COUNTIF(Sold_Items!E:E, A36)', '=COUNTIF(Sold_Items!E:E, A36)', '=IF(B35>0, C36/B36, 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A36, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!E:E, A36, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A36, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!E:E, A36, Sold_Items!M:M), 0)', '=IFERROR(COUNTIFS(Sold_Items!E:E, A36, Sold_Items!M:M, ">0")/COUNTIF(Sold_Items!E:E, A36), 0)'],
        ["", "", "", "", "", "", "", "", ""],

        # Section 5: Top 5 Garments Lowered The Most (Rows 38-45)
        ["🏆 TOP 5 GARMENTS LOWERED THE MOST (BIGGEST PRICE CUTS BEFORE SALE)", "", "", "", "", "", "", "", ""],
        ["Item SKU", "Title", "Brand", "Category", "Posted Price ($)", "Sold Price ($)", "Total Drop ($)", "Sale Type", ""],
        ['=IFERROR(QUERY(Sold_Items!A2:Z, "SELECT A, C, D, E, G, L, M, N WHERE M > 0 ORDER BY M DESC LIMIT 5 LABEL A \'\', C \'\', D \'\', E \'\', G \'\', L \'\', M \'\', N \'\'", 0), {"No discounted sales yet", "", "", "", "", "", "", ""})', "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 6: Brand Performance Leaderboard (Rows 46-54)
        ["🏷️ TOP BRAND LEADERBOARD (BY REVENUE & MARGIN)", "", "", "", "", "", "", "", ""],
        ["Brand", "Units Sold", "Avg Sale Price ($)", "Total Revenue ($)", "Avg Net Profit ($)", "Avg Price Drop ($)", "", "", ""],
        ["Carhartt", '=COUNTIF(Sold_Items!D:D, A48)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A48, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A48, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A48, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A48, Sold_Items!M:M), 0)', "", "", ""],
        ["Nike", '=COUNTIF(Sold_Items!D:D, A49)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A49, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A49, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A49, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A49, Sold_Items!M:M), 0)', "", "", ""],
        ["Levi's", '=COUNTIF(Sold_Items!D:D, A50)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A50, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A50, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A50, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A50, Sold_Items!M:M), 0)', "", "", ""],
        ["The North Face", '=COUNTIF(Sold_Items!D:D, A51)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A51, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A51, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A51, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A51, Sold_Items!M:M), 0)', "", "", ""],
        ["Patagonia", '=COUNTIF(Sold_Items!D:D, A52)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A52, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A52, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A52, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A52, Sold_Items!M:M), 0)', "", "", ""],
        ["Vintage", '=COUNTIF(Sold_Items!D:D, A53)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A53, Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!D:D, A53, Sold_Items!L:L), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A53, Sold_Items!R:R), 0)', '=IFERROR(AVERAGEIF(Sold_Items!D:D, A53, Sold_Items!M:M), 0)', "", "", ""],
        ["", "", "", "", "", "", "", "", ""],

        # Section 7: Platform Breakdown (Rows 55-61)
        ["🌐 SALES BREAKDOWN BY PLATFORM (VINTED VS FB MARKETPLACE)", "", "", "", "", "", "", "", ""],
        ["Platform Channel", "Units Sold", "Gross Revenue ($)", "Total Net Profit ($)", "Avg Profit / Item", "", "", "", ""],
        ["Vinted", '=COUNTIF(Sold_Items!P:P, "Vinted")', '=IFERROR(SUMIF(Sold_Items!P:P, "Vinted", Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!P:P, "Vinted", Sold_Items!R:R), 0)', '=IF(B57>0, D57/B57, 0)', "", "", "", ""],
        ["FB Marketplace", '=COUNTIF(Sold_Items!P:P, "FB Marketplace*")', '=IFERROR(SUMIF(Sold_Items!P:P, "FB Marketplace*", Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!P:P, "FB Marketplace*", Sold_Items!R:R), 0)', '=IF(B58>0, D58/B58, 0)', "", "", "", ""],
        ["Other Channels", '=COUNTIF(Sold_Items!P:P, "Other*")', '=IFERROR(SUMIF(Sold_Items!P:P, "Other*", Sold_Items!L:L), 0)', '=IFERROR(SUMIF(Sold_Items!P:P, "Other*", Sold_Items!R:R), 0)', '=IF(B59>0, D59/B59, 0)', "", "", "", ""],
    ]
    ws_dash.update(range_name="A1", values=dashboard_layout, value_input_option="USER_ENTERED")

    # Format Main Dashboard and Sections
    dash_formats = [
        {
            "range": "A1:I1",
            "format": {
                "backgroundColor": {"red": 0.08, "green": 0.44, "blue": 0.65},  # Teal blue
                "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 13},
                "horizontalAlignment": "CENTER",
            },
        }
    ]
    for s_range in ["A3:F4", "A13:F14", "A20:F21", "A27:I28", "A38:H39", "A46:F47", "A55:E56"]:
        dash_formats.append({
            "range": s_range,
            "format": {
                "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.32},
                "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER",
            },
        })
    dash_formats.extend([
        # Section 1
        {"range": "B5:B9", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "B10", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        {"range": "B11", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "E5:E7", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "E8:E9", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "E10:E11", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        # Section 2
        {"range": "B15:B18", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "C15:C18", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        {"range": "E15", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "E16:E17", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        {"range": "E18", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        # Section 3
        {"range": "B22:B23", "format": {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
        {"range": "B24", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "B25", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        # Section 4
        {"range": "B29:C36", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "D29:D36", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        {"range": "E29:H36", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "I29:I36", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
        # Section 5
        {"range": "E40:G44", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        # Section 6
        {"range": "B48:B53", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "C48:E53", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        {"range": "F48:F53", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
        # Section 7
        {"range": "B57:B59", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": "C57:E59", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
    ])
    safe_batch_format(ws_dash, dash_formats)

    # Remove default "Sheet1" if it exists
    if "Sheet1" in existing_worksheets and len(sh.worksheets()) > 1:
        try:
            sh.del_worksheet(existing_worksheets["Sheet1"])
        except Exception:
            pass

    return sh
