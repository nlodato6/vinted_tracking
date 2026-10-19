"""Synchronization logic for reconciling scraped Vinted data with Google Sheets."""

from datetime import datetime
from typing import List, Dict, Any, Optional
import gspread
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import (
    TAB_ITEM_LISTINGS,
    TAB_SOLD_ITEMS,
    TAB_SALES_LOG,
    TAB_INVENTORY_LOTS,
    SOLD_ITEMS_HEADERS,
)
import re
from src.models import ItemListing, SalesOrder


def parse_price(price_val: Any) -> float:
    """Extracts numeric price float from strings like '$45.00', '45,00 €', or numeric values."""
    if price_val is None:
        return 0.0
    if isinstance(price_val, (int, float)):
        return float(price_val)
    cleaned = re.sub(r"[^\d.,]", "", str(price_val)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def normalize_date_str(s: str) -> str:
    """Normalizes various date formats (e.g. 10/19/2026, 2026-10-19) to YYYY-MM-DD."""
    if not s:
        return ""
    cleaned = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return cleaned


class SheetsSyncEngine:
    def __init__(self, client: GoogleSheetsClient):
        self.client = client

    def get_listings_records(self) -> List[Dict[str, Any]]:
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_ITEM_LISTINGS)
        return ws.get_all_records()

    def get_sold_records(self) -> List[Dict[str, Any]]:
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_SOLD_ITEMS)
        return ws.get_all_records()

    def get_sales_records(self) -> List[Dict[str, Any]]:
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_SALES_LOG)
        return ws.get_all_records()

    def get_date_to_lot_map(self) -> Dict[str, str]:
        """Reads Inventory_Lots to map Purchase_Date (YYYY-MM-DD) -> Lot_ID."""
        sh = self.client.get_spreadsheet()
        ws_lots = sh.worksheet(TAB_INVENTORY_LOTS)
        lots_rows = ws_lots.get_all_values()
        date_to_lot_id: Dict[str, str] = {}

        if len(lots_rows) > 1:
            l_headers = lots_rows[0]
            l_id_idx = l_headers.index("Lot_ID") if "Lot_ID" in l_headers else 0
            p_date_idx = l_headers.index("Purchase_Date") if "Purchase_Date" in l_headers else 1

            for lr in lots_rows[1:]:
                lot_identifier = lr[l_id_idx].strip() if len(lr) > l_id_idx else ""
                purch_dt = lr[p_date_idx].strip() if len(lr) > p_date_idx else ""
                if purch_dt and lot_identifier:
                    norm_p_dt = normalize_date_str(purch_dt)
                    if norm_p_dt:
                        date_to_lot_id[norm_p_dt] = lot_identifier
                # Also allow matching if Lot_ID itself is written as a date (e.g. 2026-10-19)
                if lot_identifier:
                    norm_id_dt = normalize_date_str(lot_identifier)
                    if norm_id_dt and norm_id_dt not in date_to_lot_id:
                        date_to_lot_id[norm_id_dt] = lot_identifier

        return date_to_lot_id

    def auto_assign_lots_by_date(self) -> int:
        """
        Scans Item_Listings and Sold_Items for rows with empty Lot_ID and auto-assigns
        any that have Date_Listed matching a lot's Purchase_Date.
        """
        sh = self.client.get_spreadsheet()
        date_to_lot_id = self.get_date_to_lot_map()
        if not date_to_lot_id:
            return 0

        total_updates = 0
        for tab_name in (TAB_ITEM_LISTINGS, TAB_SOLD_ITEMS):
            try:
                ws = sh.worksheet(tab_name)
                all_rows = ws.get_all_values()
                if len(all_rows) <= 1:
                    continue

                headers = all_rows[0]
                lot_id_idx = headers.index("Lot_ID") if "Lot_ID" in headers else 1
                date_col_idx = headers.index("Date_Listed") if "Date_Listed" in headers else 13

                updates = []
                for row_idx, r in enumerate(all_rows[1:], start=2):
                    cur_lot = r[lot_id_idx].strip() if len(r) > lot_id_idx else ""
                    cur_date = r[date_col_idx].strip() if len(r) > date_col_idx else ""
                    norm_dt = normalize_date_str(cur_date)

                    if not cur_lot and norm_dt and norm_dt in date_to_lot_id:
                        col_letter = chr(ord('A') + lot_id_idx)
                        updates.append({
                            "range": f"{col_letter}{row_idx}",
                            "values": [[date_to_lot_id[norm_dt]]]
                        })

                if updates:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
                    total_updates += len(updates)
            except Exception:
                pass

        return total_updates

    def sync_lots(self) -> Dict[str, Any]:
        """
        Synchronizes Inventory_Lots:
        1. Ensures Unit_Cost formula '=IF(AND(ISNUMBER(E{row}), E{row}>0), D{row}/E{row}, "")' is present.
        2. Updates Items_Processed with '=COUNTIF(Item_Listings!B:B, A{row}) + COUNTIF(Sold_Items!B:B, A{row})'.
        3. Evaluates and updates Status to 'Completed' once all items in that lot are sold
           (sold_count >= total_articles > 0), otherwise 'Active' if items are processed, or 'Pending'.
        """
        sh = self.client.get_spreadsheet()
        ws_lots = sh.worksheet(TAB_INVENTORY_LOTS)
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        lots_rows = ws_lots.get_all_values()
        if len(lots_rows) <= 1:
            return {"total_lots": 0, "completed_lots": 0, "active_lots": 0, "updated_cells": 0}

        headers = lots_rows[0]
        lot_id_idx = headers.index("Lot_ID") if "Lot_ID" in headers else 0
        total_art_idx = headers.index("Total_Articles") if "Total_Articles" in headers else 4
        unit_cost_idx = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
        proc_idx = headers.index("Items_Processed") if "Items_Processed" in headers else 6
        status_idx = headers.index("Status") if "Status" in headers else 7

        listings_rows = ws_listings.get_all_values()
        sold_rows = ws_sold.get_all_values()

        active_counts: Dict[str, int] = {}
        if len(listings_rows) > 1:
            l_lot_idx = listings_rows[0].index("Lot_ID") if "Lot_ID" in listings_rows[0] else 1
            for lr in listings_rows[1:]:
                if len(lr) > l_lot_idx and lr[l_lot_idx].strip():
                    l_id = lr[l_lot_idx].strip()
                    active_counts[l_id] = active_counts.get(l_id, 0) + 1

        sold_counts: Dict[str, int] = {}
        if len(sold_rows) > 1:
            s_lot_idx = sold_rows[0].index("Lot_ID") if "Lot_ID" in sold_rows[0] else 1
            for sr in sold_rows[1:]:
                if len(sr) > s_lot_idx and sr[s_lot_idx].strip():
                    s_id = sr[s_lot_idx].strip()
                    sold_counts[s_id] = sold_counts.get(s_id, 0) + 1

        updates = []
        completed_count = 0
        active_lot_count = 0

        col_unit_cost = chr(ord('A') + unit_cost_idx)
        col_proc = chr(ord('A') + proc_idx)
        col_status = chr(ord('A') + status_idx)

        for row_idx, r in enumerate(lots_rows[1:], start=2):
            lot_id = r[lot_id_idx].strip() if len(r) > lot_id_idx else ""
            if not lot_id:
                continue

            total_articles_str = r[total_art_idx].strip() if len(r) > total_art_idx else ""
            try:
                total_articles = int(float(total_articles_str)) if total_articles_str else 0
            except ValueError:
                total_articles = 0

            sold_for_lot = sold_counts.get(lot_id, 0)
            active_for_lot = active_counts.get(lot_id, 0)
            processed_for_lot = sold_for_lot + active_for_lot

            if total_articles > 0 and sold_for_lot >= total_articles:
                completed_count += 1
            elif processed_for_lot > 0:
                active_lot_count += 1

            expected_cost_formula = f'=IF(A{row_idx}="", "", IF(AND(ISNUMBER(E{row_idx}), E{row_idx}>0), D{row_idx}/E{row_idx}, ""))'
            expected_proc_formula = f'=IF(A{row_idx}="", "", COUNTIF(Item_Listings!B:B, A{row_idx}) + COUNTIF(Sold_Items!B:B, A{row_idx}))'
            expected_status_formula = (
                f'=IF(A{row_idx}="", "", IF(AND(ISNUMBER(E{row_idx}), E{row_idx}>0, COUNTIF(Sold_Items!B:B, A{row_idx})>=E{row_idx}), '
                f'"Completed", IF(COUNTIF(Item_Listings!B:B, A{row_idx})+COUNTIF(Sold_Items!B:B, A{row_idx})>0, '
                f'"Active", "Pending")))'
            )

            cur_cost = r[unit_cost_idx].strip() if len(r) > unit_cost_idx else ""
            cur_proc = r[proc_idx].strip() if len(r) > proc_idx else ""
            cur_status = r[status_idx].strip() if len(r) > status_idx else ""

            if not cur_cost.startswith("="):
                updates.append({"range": f"{col_unit_cost}{row_idx}", "values": [[expected_cost_formula]]})
            if not cur_proc.startswith("="):
                updates.append({"range": f"{col_proc}{row_idx}", "values": [[expected_proc_formula]]})
            if not cur_status.startswith("="):
                updates.append({"range": f"{col_status}{row_idx}", "values": [[expected_status_formula]]})

        if updates:
            ws_lots.batch_update(updates, value_input_option="USER_ENTERED")

        return {
            "total_lots": len([r for r in lots_rows[1:] if len(r) > lot_id_idx and r[lot_id_idx].strip()]),
            "completed_lots": completed_count,
            "active_lots": active_lot_count,
            "updated_cells": len(updates),
        }

    def get_max_sku_num(self) -> int:
        """Finds highest SKU number across Item_Listings, Sold_Items, and Sales_Log."""
        sh = self.client.get_spreadsheet()
        max_sku = 1000

        for tab_name in (TAB_ITEM_LISTINGS, TAB_SOLD_ITEMS):
            try:
                ws = sh.worksheet(tab_name)
                rows = ws.get_all_values()
                if len(rows) > 1:
                    sku_idx = rows[0].index("Item_SKU") if "Item_SKU" in rows[0] else 0
                    for r in rows[1:]:
                        if len(r) > sku_idx and r[sku_idx].strip().upper().startswith("SKU-"):
                            try:
                                num = int(r[sku_idx].strip().upper().replace("SKU-", ""))
                                if num > max_sku:
                                    max_sku = num
                            except ValueError:
                                pass
            except Exception:
                pass

        try:
            ws_sales = sh.worksheet(TAB_SALES_LOG)
            s_rows = ws_sales.get_all_values()
            if len(s_rows) > 1:
                s_sku_idx = s_rows[0].index("Item_SKU") if "Item_SKU" in s_rows[0] else 2
                for r in s_rows[1:]:
                    if len(r) > s_sku_idx and r[s_sku_idx].strip().upper().startswith("SKU-"):
                        try:
                            num = int(r[s_sku_idx].strip().upper().replace("SKU-", ""))
                            if num > max_sku:
                                max_sku = num
                        except ValueError:
                            pass
        except Exception:
            pass

        return max_sku

    def move_listing_to_sold(
        self,
        sku: Optional[str] = None,
        vinted_id: Optional[str] = None,
        title: Optional[str] = None,
        status: str = "Sold",
        sold_price: Optional[float] = None,
        sale_date: Optional[str] = None,
        platform: str = "Vinted",
        order_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Finds an active listing in Item_Listings by SKU, Vinted ID, or Title,
        moves it to Sold_Items with full sale metadata, profit formulas, and sale type,
        and removes it from Item_Listings.
        """
        sh = self.client.get_spreadsheet()
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        listings_rows = ws_listings.get_all_values()
        if len(listings_rows) <= 1:
            return None

        headers = listings_rows[0]
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        title_idx = headers.index("Title") if "Title" in headers else 2
        id_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else 13
        cost_idx = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
        posted_idx = headers.index("Posted_Price") if "Posted_Price" in headers else 6
        curr_idx = headers.index("Current_Price") if "Current_Price" in headers else 7

        clean_sku = sku.strip().upper() if sku else ""
        clean_id = vinted_id.strip() if vinted_id else ""
        clean_title = title.strip().lower() if title else ""

        target_row_idx = None
        target_row_data = None

        for r_idx, r in enumerate(listings_rows[1:], start=2):
            r_sku = r[sku_idx].strip().upper() if len(r) > sku_idx else ""
            r_id = r[id_idx].strip() if len(r) > id_idx else ""
            r_title = r[title_idx].strip().lower() if len(r) > title_idx else ""

            if (clean_sku and r_sku == clean_sku) or \
               (clean_id and r_id == clean_id) or \
               (clean_title and r_title == clean_title):
                target_row_idx = r_idx
                target_row_data = list(r)
                break

        if not target_row_idx or not target_row_data:
            return None

        sold_rows = ws_sold.get_all_values()
        next_sold_row = len(sold_rows) + 1

        lot_id = target_row_data[headers.index("Lot_ID")] if "Lot_ID" in headers and len(target_row_data) > headers.index("Lot_ID") else ""
        item_title = target_row_data[title_idx] if len(target_row_data) > title_idx else ""
        brand = target_row_data[headers.index("Brand")] if "Brand" in headers and len(target_row_data) > headers.index("Brand") else ""
        category = target_row_data[headers.index("Category")] if "Category" in headers and len(target_row_data) > headers.index("Category") else ""
        posted_price = target_row_data[posted_idx] if len(target_row_data) > posted_idx else ""
        curr_price = target_row_data[curr_idx] if len(target_row_data) > curr_idx else ""
        price_hist = target_row_data[headers.index("Price_History")] if "Price_History" in headers and len(target_row_data) > headers.index("Price_History") else ""
        favs = target_row_data[headers.index("Favorites")] if "Favorites" in headers and len(target_row_data) > headers.index("Favorites") else 0
        views = target_row_data[headers.index("Views")] if "Views" in headers and len(target_row_data) > headers.index("Views") else 0
        v_url = target_row_data[headers.index("Vinted_URL")] if "Vinted_URL" in headers and len(target_row_data) > headers.index("Vinted_URL") else ""
        dt_listed = target_row_data[headers.index("Date_Listed")] if "Date_Listed" in headers and len(target_row_data) > headers.index("Date_Listed") else ""

        sp_val = sold_price if sold_price is not None else (parse_price(curr_price) or parse_price(posted_price))
        s_date_str = sale_date or datetime.now().strftime("%Y-%m-%d")
        plat_str = platform or "Vinted"
        ord_ref = order_id or ""

        sold_row_data = [
            target_row_data[sku_idx],                                         # A: Item_SKU
            lot_id,                                                           # B: Lot_ID
            item_title,                                                       # C: Title
            brand,                                                            # D: Brand
            category,                                                         # E: Category
            f'=IFERROR(VLOOKUP(B{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)', # F: Unit_Cost
            posted_price,                                                     # G: Posted_Price
            curr_price,                                                       # H: Current_Price
            f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), "YES", "NO"))', # I: Price_Lowered
            f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), G{next_sold_row}-H{next_sold_row}, 0))', # J: Price_Drop
            price_hist,                                                       # K: Price_History
            sp_val,                                                           # L: Sold_Price
            f'=IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(L{next_sold_row}), G{next_sold_row}>0), G{next_sold_row}-L{next_sold_row}, 0)', # M: Price_Discount
            f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(L{next_sold_row}>=G{next_sold_row}, "Original Price", IF(L{next_sold_row}=H{next_sold_row}, "Lowered Price", "Accepted Offer")))', # N: Sale_Type
            s_date_str,                                                       # O: Sale_Date
            plat_str,                                                         # P: Platform
            f'=IF(P{next_sold_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)', # Q: Packaging_Cost
            f'=IF(AND(ISNUMBER(L{next_sold_row}), ISNUMBER(F{next_sold_row}), ISNUMBER(Q{next_sold_row})), L{next_sold_row}-F{next_sold_row}-Q{next_sold_row}, IF(ISNUMBER(L{next_sold_row}), L{next_sold_row}, 0))', # R: Net_Profit
            f'=IF(AND(ISNUMBER(L{next_sold_row}), L{next_sold_row}>0), R{next_sold_row}/L{next_sold_row}, 0)', # S: Margin_%
            favs,                                                             # T: Favorites
            views,                                                            # U: Views
            ord_ref,                                                          # V: Order_ID
            v_url,                                                            # W: Vinted_URL
            status,                                                           # X: Status
            dt_listed,                                                        # Y: Date_Listed
        ]

        ws_sold.append_row(sold_row_data, value_input_option="USER_ENTERED")
        ws_listings.delete_rows(target_row_idx)

        try:
            self.sync_lots()
        except Exception:
            pass

        return {SOLD_ITEMS_HEADERS[i]: sold_row_data[i] for i in range(len(SOLD_ITEMS_HEADERS))}

    def reconcile_sold_listings(self) -> int:
        """
        Finds any listings in Item_Listings where Status contains 'Sold' (e.g. manually set by user)
        and migrates them into Sold_Items, then removes them from Item_Listings.
        """
        sh = self.client.get_spreadsheet()
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

        listings_rows = ws_listings.get_all_values()
        if len(listings_rows) <= 1:
            return 0

        headers = listings_rows[0]
        status_idx = headers.index("Status") if "Status" in headers else 15
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        curr_idx = headers.index("Current_Price") if "Current_Price" in headers else 7

        moved_count = 0
        # Iterate over copy of rows
        for r in list(listings_rows[1:]):
            cur_status = r[status_idx].strip() if len(r) > status_idx else ""
            if "sold" in cur_status.lower():
                cur_sku = r[sku_idx].strip().upper() if len(r) > sku_idx else ""
                cur_price = parse_price(r[curr_idx]) if len(r) > curr_idx else 0.0
                res = self.move_listing_to_sold(
                    sku=cur_sku,
                    status=cur_status,
                    sold_price=cur_price,
                )
                if res:
                    moved_count += 1

        return moved_count

    def sync_listings(self, scraped_items: List[ItemListing]) -> Dict[str, int]:
        """
        Updates live prices, price drop tracking, and statuses of active listings in Item_Listings.
        When an item sells or has sold status, it moves/appends to Sold_Items.
        Automatically assigns Lot_ID when Date_Listed matches Inventory_Lots purchase date.
        """
        sh = self.client.get_spreadsheet()
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        listings_rows = ws_listings.get_all_values()
        sold_rows = ws_sold.get_all_values()

        date_to_lot_id = self.get_date_to_lot_map()
        max_sku_num = self.get_max_sku_num()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        l_headers = listings_rows[0] if listings_rows else SOLD_ITEMS_HEADERS
        sku_col_idx = l_headers.index("Item_SKU") if "Item_SKU" in l_headers else 0
        lot_id_col_idx = l_headers.index("Lot_ID") if "Lot_ID" in l_headers else 1
        title_col_idx = l_headers.index("Title") if "Title" in l_headers else 2
        brand_col_idx = l_headers.index("Brand") if "Brand" in l_headers else 3
        cat_col_idx = l_headers.index("Category") if "Category" in l_headers else 4
        cost_col_idx = l_headers.index("Unit_Cost") if "Unit_Cost" in l_headers else 5
        posted_col_idx = l_headers.index("Posted_Price") if "Posted_Price" in l_headers else (l_headers.index("Target_Price") if "Target_Price" in l_headers else 6)
        price_col_idx = l_headers.index("Current_Price") if "Current_Price" in l_headers else 7
        lowered_col_idx = l_headers.index("Price_Lowered") if "Price_Lowered" in l_headers else 8
        drop_col_idx = l_headers.index("Price_Drop") if "Price_Drop" in l_headers else 9
        hist_col_idx = l_headers.index("Price_History") if "Price_History" in l_headers else 10
        sold_price_col_idx = l_headers.index("Sold_Price") if "Sold_Price" in l_headers else 11
        fav_col_idx = l_headers.index("Favorites") if "Favorites" in l_headers else 12
        views_col_idx = l_headers.index("Views") if "Views" in l_headers else 13
        id_col_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 14
        url_col_idx = l_headers.index("Vinted_URL") if "Vinted_URL" in l_headers else 15
        status_col_idx = l_headers.index("Status") if "Status" in l_headers else 16
        date_col_idx = l_headers.index("Date_Listed") if "Date_Listed" in l_headers else 17
        synced_col_idx = l_headers.index("Last_Synced") if "Last_Synced" in l_headers else 18

        # Map active listings
        listings_by_vinted_id: Dict[str, int] = {}
        listings_by_title: Dict[str, int] = {}
        if len(listings_rows) > 1:
            for row_idx, row in enumerate(listings_rows[1:], start=2):
                if len(row) > id_col_idx and row[id_col_idx].strip():
                    listings_by_vinted_id[row[id_col_idx].strip()] = row_idx
                if len(row) > title_col_idx and row[title_col_idx].strip():
                    listings_by_title[row[title_col_idx].strip().lower()] = row_idx

        # Map existing sold items
        sold_by_vinted_id: Dict[str, int] = {}
        sold_by_title: Dict[str, int] = {}
        if len(sold_rows) > 1:
            for row_idx, row in enumerate(sold_rows[1:], start=2):
                if len(row) > id_col_idx and row[id_col_idx].strip():
                    sold_by_vinted_id[row[id_col_idx].strip()] = row_idx
                if len(row) > title_col_idx and row[title_col_idx].strip():
                    sold_by_title[row[title_col_idx].strip().lower()] = row_idx

        listing_updates = []
        sold_updates = []
        new_active_rows = []
        new_sold_rows = []
        moved_to_sold_count = 0
        updated_count = 0

        col_g = chr(ord('A') + posted_col_idx)
        col_h = chr(ord('A') + price_col_idx)

        for item in scraped_items:
            v_id = item.vinted_id.strip() if item.vinted_id else ""
            clean_title = item.title.strip().lower()
            item_date_str = item.date_listed.strftime("%Y-%m-%d") if item.date_listed else datetime.now().strftime("%Y-%m-%d")
            is_sold = bool(item.status and "sold" in item.status.lower())

            in_active_row = listings_by_vinted_id.get(v_id) or listings_by_title.get(clean_title)
            in_sold_row = sold_by_vinted_id.get(v_id) or sold_by_title.get(clean_title)

            posted_val = item.posted_price if (item.posted_price and item.posted_price > 0) else item.current_price
            curr_val = item.current_price

            if is_sold:
                if in_active_row:
                    # Move active listing to Sold_Items
                    res = self.move_listing_to_sold(
                        vinted_id=v_id,
                        title=clean_title,
                        status="Sold",
                        sold_price=item.sold_price if item.sold_price is not None else curr_val,
                    )
                    if res:
                        moved_to_sold_count += 1
                        listings_by_vinted_id.pop(v_id, None)
                        listings_by_title.pop(clean_title, None)
                elif in_sold_row:
                    col_price = chr(ord('A') + price_col_idx)
                    col_fav = chr(ord('A') + fav_col_idx)
                    col_views = chr(ord('A') + views_col_idx)
                    col_synced = chr(ord('A') + synced_col_idx)
                    sold_updates.append({"range": f"{col_price}{in_sold_row}", "values": [[curr_val]]})
                    if sold_price_col_idx is not None and item.sold_price is not None:
                        col_sp = chr(ord('A') + sold_price_col_idx)
                        sold_updates.append({"range": f"{col_sp}{in_sold_row}", "values": [[item.sold_price]]})
                    sold_updates.append({"range": f"{col_fav}{in_sold_row}", "values": [[item.favorites]]})
                    sold_updates.append({"range": f"{col_views}{in_sold_row}", "values": [[item.views]]})
                    sold_updates.append({"range": f"{col_synced}{in_sold_row}", "values": [[now_str]]})
                    updated_count += 1
                else:
                    # New sold item never tracked in active listings
                    max_sku_num += 1
                    new_sku = f"SKU-{max_sku_num}"
                    next_sold_row_idx = len(sold_rows) + len(new_sold_rows) + 1
                    assigned_lot = item.lot_id or date_to_lot_id.get(item_date_str, "")
                    col_lot = chr(ord('A') + lot_id_col_idx)

                    hist_str = ""
                    if posted_val > curr_val > 0:
                        hist_str = f"{datetime.now().strftime('%m/%d')}: ${posted_val:.2f} → ${curr_val:.2f}"

                    sold_row_data = [
                        new_sku,
                        assigned_lot,
                        item.title,
                        item.brand or "",
                        item.category or "",
                        f"=IFERROR(VLOOKUP({col_lot}{next_sold_row_idx}, Inventory_Lots!A:F, 6, FALSE), 0)",
                        posted_val,
                        curr_val,
                        f'=IF(OR({col_g}{next_sold_row_idx}="", {col_h}{next_sold_row_idx}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row_idx}), ISNUMBER({col_h}{next_sold_row_idx}), {col_h}{next_sold_row_idx}<{col_g}{next_sold_row_idx}), "YES", "NO"))',
                        f'=IF(OR({col_g}{next_sold_row_idx}="", {col_h}{next_sold_row_idx}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row_idx}), ISNUMBER({col_h}{next_sold_row_idx}), {col_h}{next_sold_row_idx}<{col_g}{next_sold_row_idx}), {col_g}{next_sold_row_idx}-{col_h}{next_sold_row_idx}, 0))',
                        hist_str,
                        item.sold_price if item.sold_price is not None else curr_val,
                        f'=IF(AND(ISNUMBER({col_g}{next_sold_row_idx}), ISNUMBER(L{next_sold_row_idx}), {col_g}{next_sold_row_idx}>0), {col_g}{next_sold_row_idx}-L{next_sold_row_idx}, 0)',
                        f'=IF(OR({col_g}{next_sold_row_idx}="", L{next_sold_row_idx}=""), "", IF(L{next_sold_row_idx}>={col_g}{next_sold_row_idx}, "Original Price", IF(L{next_sold_row_idx}={col_h}{next_sold_row_idx}, "Lowered Price", "Accepted Offer")))',
                        item_date_str,
                        "Vinted",
                        f'=IF(P{next_sold_row_idx}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
                        f'=IF(AND(ISNUMBER(L{next_sold_row_idx}), ISNUMBER(F{next_sold_row_idx}), ISNUMBER(Q{next_sold_row_idx})), L{next_sold_row_idx}-F{next_sold_row_idx}-Q{next_sold_row_idx}, IF(ISNUMBER(L{next_sold_row_idx}), L{next_sold_row_idx}, 0))',
                        f'=IF(AND(ISNUMBER(L{next_sold_row_idx}), L{next_sold_row_idx}>0), R{next_sold_row_idx}/L{next_sold_row_idx}, 0)',
                        item.favorites,
                        item.views,
                        "",
                        item.vinted_url,
                        "Sold",
                        item_date_str,
                    ]
                    new_sold_rows.append(sold_row_data)
            else:
                # Active listing (e.g. Listed or Reserved)
                if in_active_row:
                    existing_data = listings_rows[in_active_row - 1]
                    old_curr_str = existing_data[price_col_idx] if len(existing_data) > price_col_idx else ""
                    old_curr_val = parse_price(old_curr_str)
                    old_hist = existing_data[hist_col_idx] if len(existing_data) > hist_col_idx else ""

                    # Detect price reduction and record in Price_History
                    if old_curr_val > 0 and curr_val > 0 and curr_val < old_curr_val:
                        today_md = datetime.now().strftime("%m/%d")
                        drop_entry = f"{today_md}: ${old_curr_val:.2f} → ${curr_val:.2f}"
                        updated_hist = f"{old_hist} | {drop_entry}" if old_hist.strip() else drop_entry
                        col_hist = chr(ord('A') + hist_col_idx)
                        listing_updates.append({"range": f"{col_hist}{in_active_row}", "values": [[updated_hist]]})

                    col_price = chr(ord('A') + price_col_idx)
                    col_fav = chr(ord('A') + fav_col_idx)
                    col_views = chr(ord('A') + views_col_idx)
                    col_status = chr(ord('A') + status_col_idx)
                    col_synced = chr(ord('A') + synced_col_idx)

                    # Ensure formulas for Price_Lowered and Price_Drop are set
                    cur_lowered_val = existing_data[lowered_col_idx] if len(existing_data) > lowered_col_idx else ""
                    cur_drop_val = existing_data[drop_col_idx] if len(existing_data) > drop_col_idx else ""
                    if not cur_lowered_val.startswith("="):
                        col_low = chr(ord('A') + lowered_col_idx)
                        listing_updates.append({"range": f"{col_low}{in_active_row}", "values": [[f'=IF(OR({col_g}{in_active_row}="", {col_h}{in_active_row}=""), "", IF(AND(ISNUMBER({col_g}{in_active_row}), ISNUMBER({col_h}{in_active_row}), {col_h}{in_active_row}<{col_g}{in_active_row}), "YES", "NO"))']]})
                    if not cur_drop_val.startswith("="):
                        col_drp = chr(ord('A') + drop_col_idx)
                        listing_updates.append({"range": f"{col_drp}{in_active_row}", "values": [[f'=IF(OR({col_g}{in_active_row}="", {col_h}{in_active_row}=""), "", IF(AND(ISNUMBER({col_g}{in_active_row}), ISNUMBER({col_h}{in_active_row}), {col_h}{in_active_row}<{col_g}{in_active_row}), {col_g}{in_active_row}-{col_h}{in_active_row}, 0))']]})

                    # Check for unassigned lot match
                    cur_lot = existing_data[lot_id_col_idx].strip() if len(existing_data) > lot_id_col_idx else ""
                    cur_date = existing_data[date_col_idx].strip() if len(existing_data) > date_col_idx else ""
                    row_dt = normalize_date_str(cur_date) or item_date_str
                    if not cur_lot and row_dt in date_to_lot_id:
                        col_lot = chr(ord('A') + lot_id_col_idx)
                        listing_updates.append({"range": f"{col_lot}{in_active_row}", "values": [[date_to_lot_id[row_dt]]]})

                    listing_updates.append({"range": f"{col_price}{in_active_row}", "values": [[curr_val]]})
                    listing_updates.append({"range": f"{col_fav}{in_active_row}", "values": [[item.favorites]]})
                    listing_updates.append({"range": f"{col_views}{in_active_row}", "values": [[item.views]]})
                    if item.status:
                        listing_updates.append({"range": f"{col_status}{in_active_row}", "values": [[item.status]]})
                    listing_updates.append({"range": f"{col_synced}{in_active_row}", "values": [[now_str]]})
                    updated_count += 1
                else:
                    # Append new active listing
                    max_sku_num += 1
                    new_sku = f"SKU-{max_sku_num}"
                    new_active_idx = len(listings_rows) + len(new_active_rows) + 1
                    assigned_lot = item.lot_id or date_to_lot_id.get(item_date_str, "")
                    col_lot = chr(ord('A') + lot_id_col_idx)

                    hist_str = ""
                    if posted_val > curr_val > 0:
                        hist_str = f"{datetime.now().strftime('%m/%d')}: ${posted_val:.2f} → ${curr_val:.2f}"

                    active_row_data = [
                        new_sku,
                        assigned_lot,
                        item.title,
                        item.brand or "",
                        item.category or "",
                        f"=IFERROR(VLOOKUP({col_lot}{new_active_idx}, Inventory_Lots!A:F, 6, FALSE), 0)",
                        posted_val,
                        curr_val,
                        f'=IF(OR({col_g}{new_active_idx}="", {col_h}{new_active_idx}=""), "", IF(AND(ISNUMBER({col_g}{new_active_idx}), ISNUMBER({col_h}{new_active_idx}), {col_h}{new_active_idx}<{col_g}{new_active_idx}), "YES", "NO"))',
                        f'=IF(OR({col_g}{new_active_idx}="", {col_h}{new_active_idx}=""), "", IF(AND(ISNUMBER({col_g}{new_active_idx}), ISNUMBER({col_h}{new_active_idx}), {col_h}{new_active_idx}<{col_g}{new_active_idx}), {col_g}{new_active_idx}-{col_h}{new_active_idx}, 0))',
                        hist_str,
                        item.favorites,
                        item.views,
                        item.vinted_id,
                        item.vinted_url,
                        item.status or "Listed",
                        item_date_str,
                        now_str,
                    ]
                    new_active_rows.append(active_row_data)

        if listing_updates:
            ws_listings.batch_update(listing_updates, value_input_option="USER_ENTERED")
        if new_active_rows:
            ws_listings.append_rows(new_active_rows, value_input_option="USER_ENTERED")

        if sold_updates:
            ws_sold.batch_update(sold_updates, value_input_option="USER_ENTERED")
        if new_sold_rows:
            ws_sold.append_rows(new_sold_rows, value_input_option="USER_ENTERED")

        # Reconcile any remaining listings marked 'Sold' manually
        reconciled = self.reconcile_sold_listings()
        moved_to_sold_count += reconciled

        # Backfill lot IDs by purchase date
        self.auto_assign_lots_by_date()

        # Update Inventory_Lots: unit costs, items processed, status (Completed when all sold)
        self.sync_lots()

        return {
            "updated": updated_count,
            "created": len(new_active_rows) + len(new_sold_rows),
            "moved_to_sold": moved_to_sold_count,
        }

    def sync_orders(self, scraped_orders: List[SalesOrder]) -> Dict[str, int]:
        """
        Reconciles scraped completed orders directly with Sold_Items.
        Moves matching Item_Listings to Sold_Items, or appends directly if not currently in active listings.
        """
        sh = self.client.get_spreadsheet()
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        sold_rows = ws_sold.get_all_values()
        existing_order_ids = set()
        s_headers = sold_rows[0] if sold_rows else SOLD_ITEMS_HEADERS
        order_id_idx = s_headers.index("Order_ID") if "Order_ID" in s_headers else 21
        sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
        title_idx = s_headers.index("Title") if "Title" in s_headers else 2
        sp_idx = s_headers.index("Sold_Price") if "Sold_Price" in s_headers else 11

        if len(sold_rows) > 1:
            for row in sold_rows[1:]:
                if len(row) > order_id_idx and row[order_id_idx].strip():
                    existing_order_ids.add(row[order_id_idx].strip())

        synced_count = 0
        max_sku_num = self.get_max_sku_num()

        for order in scraped_orders:
            if order.order_id.strip() in existing_order_ids:
                continue

            sale_date_str = order.sale_date.strftime("%Y-%m-%d") if order.sale_date else datetime.now().strftime("%Y-%m-%d")
            clean_title = order.item_title.strip()

            # 1. Attempt to move matching active listing to Sold_Items
            moved_item = self.move_listing_to_sold(
                sku=order.item_sku,
                title=clean_title,
                status="Sold",
                sold_price=order.sold_price,
                sale_date=sale_date_str,
                platform=order.platform or "Vinted",
                order_id=order.order_id,
            )

            if moved_item:
                synced_count += 1
                existing_order_ids.add(order.order_id.strip())
                continue

            # 2. Check if already in Sold_Items, update Order_ID and Sold_Price
            matched = False
            cur_sold_rows = ws_sold.get_all_values()
            clean_low = clean_title.lower()
            for r_i, sr in enumerate(cur_sold_rows[1:], start=2):
                sr_sku = sr[sku_idx].strip().upper() if len(sr) > sku_idx else ""
                sr_title = sr[title_idx].strip().lower() if len(sr) > title_idx else ""
                if (order.item_sku and sr_sku == order.item_sku.strip().upper()) or (clean_low and sr_title == clean_low):
                    col_sp = chr(ord('A') + sp_idx)
                    col_ord = chr(ord('A') + order_id_idx)
                    ws_sold.update_acell(f"{col_sp}{r_i}", order.sold_price)
                    ws_sold.update_acell(f"{col_ord}{r_i}", order.order_id)
                    matched = True
                    synced_count += 1
                    existing_order_ids.add(order.order_id.strip())
                    break

            if matched:
                continue

            # 3. If neither active listing nor existing sold item found, create directly in Sold_Items
            max_sku_num += 1
            new_sku = order.item_sku or f"SKU-{max_sku_num}"
            next_row = len(ws_sold.get_all_values()) + 1

            new_sold_data = [
                new_sku,                                                      # A: Item_SKU
                "",                                                           # B: Lot_ID
                order.item_title,                                             # C: Title
                order.brand or "",                                            # D: Brand
                order.category or "",                                         # E: Category
                0.0,                                                          # F: Unit_Cost
                order.sold_price,                                             # G: Posted_Price
                order.sold_price,                                             # H: Current_Price
                "NO",                                                         # I: Price_Lowered
                0.0,                                                          # J: Price_Drop
                "",                                                           # K: Price_History
                order.sold_price,                                             # L: Sold_Price
                f'=IF(AND(ISNUMBER(G{next_row}), ISNUMBER(L{next_row}), G{next_row}>0), G{next_row}-L{next_row}, 0)', # M: Price_Discount
                f'=IF(OR(G{next_row}="", L{next_row}=""), "", IF(L{next_row}>=G{next_row}, "Original Price", IF(L{next_row}=H{next_row}, "Lowered Price", "Accepted Offer")))', # N: Sale_Type
                sale_date_str,                                                # O: Sale_Date
                order.platform or "Vinted",                                   # P: Platform
                f'=IF(P{next_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)', # Q: Packaging_Cost
                f'=IF(AND(ISNUMBER(L{next_row}), ISNUMBER(F{next_row}), ISNUMBER(Q{next_row})), L{next_row}-F{next_row}-Q{next_row}, IF(ISNUMBER(L{next_row}), L{next_row}, 0))', # R: Net_Profit
                f'=IF(AND(ISNUMBER(L{next_row}), L{next_row}>0), R{next_row}/L{next_row}, 0)', # S: Margin_%
                order.favorites or 0,                                         # T: Favorites
                0,                                                            # U: Views
                order.order_id,                                               # V: Order_ID
                "",                                                           # W: Vinted_URL
                "Sold",                                                       # X: Status
                sale_date_str,                                                # Y: Date_Listed
            ]
            ws_sold.append_row(new_sold_data, value_input_option="USER_ENTERED")
            synced_count += 1
            existing_order_ids.add(order.order_id.strip())

        # Also sweep any listings marked Sold
        self.reconcile_sold_listings()

        # Update Inventory_Lots
        self.sync_lots()

        return {"synced_orders": synced_count}
