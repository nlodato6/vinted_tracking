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
    ) -> Optional[Dict[str, Any]]:
        """
        Finds an active listing in Item_Listings by SKU, Vinted ID, or Title,
        moves it to Sold_Items with updated status, sold price, and timestamp,
        and removes it from Item_Listings.
        Returns the moved item data as a dictionary, or None if not found.
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
        id_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else (headers.index("Vinted_ID") if "Vinted_ID" in headers else 14)
        status_idx = headers.index("Status") if "Status" in headers else (headers.index("Status") if "Status" in headers else 16)
        synced_idx = headers.index("Last_Synced") if "Last_Synced" in headers else (len(headers) - 1)
        cost_idx = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
        posted_idx = headers.index("Posted_Price") if "Posted_Price" in headers else (headers.index("Target_Price") if "Target_Price" in headers else 6)
        curr_idx = headers.index("Current_Price") if "Current_Price" in headers else 7
        lowered_idx = headers.index("Price_Lowered") if "Price_Lowered" in headers else None
        drop_idx = headers.index("Price_Drop") if "Price_Drop" in headers else None
        sold_price_idx = headers.index("Sold_Price") if "Sold_Price" in headers else None

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

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sold_rows = ws_sold.get_all_values()
        next_sold_row = len(sold_rows) + 1

        while len(target_row_data) < len(headers):
            target_row_data.append("")

        target_row_data[status_idx] = status
        target_row_data[synced_idx] = now_str

        # Update Unit_Cost formula reference if pointing to lot row
        col_lot = chr(ord('A') + (headers.index("Lot_ID") if "Lot_ID" in headers else 1))
        cur_cost_val = str(target_row_data[cost_idx])
        if cur_cost_val.startswith("=") and "Inventory_Lots" in cur_cost_val:
            target_row_data[cost_idx] = f'=IFERROR(VLOOKUP({col_lot}{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)'

        # Set Sold_Price if provided
        if sold_price_idx is not None and sold_price is not None:
            target_row_data[sold_price_idx] = sold_price

        # Update Price_Lowered and Price_Drop formulas pointing to next_sold_row
        col_g = chr(ord('A') + posted_idx)
        col_h = chr(ord('A') + curr_idx)
        if lowered_idx is not None:
            target_row_data[lowered_idx] = f'=IF(OR({col_g}{next_sold_row}="", {col_h}{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER({col_h}{next_sold_row}), {col_h}{next_sold_row}<{col_g}{next_sold_row}), "YES", "NO"))'
        if drop_idx is not None:
            target_row_data[drop_idx] = f'=IF(OR({col_g}{next_sold_row}="", {col_h}{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER({col_h}{next_sold_row}), {col_h}{next_sold_row}<{col_g}{next_sold_row}), {col_g}{next_sold_row}-{col_h}{next_sold_row}, 0))'

        ws_sold.append_row(target_row_data, value_input_option="USER_ENTERED")
        ws_listings.delete_rows(target_row_idx)

        try:
            self.sync_lots()
        except Exception:
            pass

        return {headers[i]: target_row_data[i] for i in range(min(len(headers), len(target_row_data)))}

    def reconcile_sold_listings(self) -> int:
        """
        Finds any listings in Item_Listings where Status contains 'Sold' (e.g. manually set by user)
        and migrates them into Sold_Items, then removes them from Item_Listings.
        """
        sh = self.client.get_spreadsheet()
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        listings_rows = ws_listings.get_all_values()
        if len(listings_rows) <= 1:
            return 0

        sold_rows = ws_sold.get_all_values()
        headers = listings_rows[0]
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        id_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else (headers.index("Vinted_ID") if "Vinted_ID" in headers else 14)
        status_idx = headers.index("Status") if "Status" in headers else (headers.index("Status") if "Status" in headers else 16)
        synced_idx = headers.index("Last_Synced") if "Last_Synced" in headers else (len(headers) - 1)
        cost_idx = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
        posted_idx = headers.index("Posted_Price") if "Posted_Price" in headers else (headers.index("Target_Price") if "Target_Price" in headers else 6)
        curr_idx = headers.index("Current_Price") if "Current_Price" in headers else 7
        lowered_idx = headers.index("Price_Lowered") if "Price_Lowered" in headers else None
        drop_idx = headers.index("Price_Drop") if "Price_Drop" in headers else None
        sold_price_idx = headers.index("Sold_Price") if "Sold_Price" in headers else None

        existing_sold_skus = set()
        existing_sold_ids = set()
        if len(sold_rows) > 1:
            for r in sold_rows[1:]:
                if len(r) > sku_idx and r[sku_idx].strip():
                    existing_sold_skus.add(r[sku_idx].strip().upper())
                if len(r) > id_idx and r[id_idx].strip():
                    existing_sold_ids.add(r[id_idx].strip())

        rows_to_delete = []
        rows_to_append = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for r_idx, r in enumerate(listings_rows[1:], start=2):
            cur_status = r[status_idx].strip() if len(r) > status_idx else ""
            if "sold" in cur_status.lower():
                cur_sku = r[sku_idx].strip().upper() if len(r) > sku_idx else ""
                cur_id = r[id_idx].strip() if len(r) > id_idx else ""

                if (not cur_sku or cur_sku not in existing_sold_skus) and (not cur_id or cur_id not in existing_sold_ids):
                    new_r = list(r)
                    while len(new_r) < len(headers):
                        new_r.append("")
                    next_sold_row = len(sold_rows) + len(rows_to_append) + 1

                    col_lot = chr(ord('A') + (headers.index("Lot_ID") if "Lot_ID" in headers else 1))
                    cur_cost = str(new_r[cost_idx])
                    if cur_cost.startswith("=") and "Inventory_Lots" in cur_cost:
                        new_r[cost_idx] = f'=IFERROR(VLOOKUP({col_lot}{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)'

                    col_g = chr(ord('A') + posted_idx)
                    col_h = chr(ord('A') + curr_idx)
                    if lowered_idx is not None:
                        new_r[lowered_idx] = f'=IF(OR({col_g}{next_sold_row}="", {col_h}{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER({col_h}{next_sold_row}), {col_h}{next_sold_row}<{col_g}{next_sold_row}), "YES", "NO"))'
                    if drop_idx is not None:
                        new_r[drop_idx] = f'=IF(OR({col_g}{next_sold_row}="", {col_h}{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER({col_h}{next_sold_row}), {col_h}{next_sold_row}<{col_g}{next_sold_row}), {col_g}{next_sold_row}-{col_h}{next_sold_row}, 0))'

                    if sold_price_idx is not None and (not new_r[sold_price_idx] or new_r[sold_price_idx] == "-"):
                        new_r[sold_price_idx] = new_r[curr_idx]

                    new_r[synced_idx] = now_str
                    rows_to_append.append(new_r)
                    if cur_sku:
                        existing_sold_skus.add(cur_sku)
                    if cur_id:
                        existing_sold_ids.add(cur_id)

                rows_to_delete.append(r_idx)

        if rows_to_append:
            ws_sold.append_rows(rows_to_append, value_input_option="USER_ENTERED")

        for r_idx in sorted(rows_to_delete, reverse=True):
            ws_listings.delete_rows(r_idx)

        return len(rows_to_delete)

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
                        item.favorites,
                        item.views,
                        item.vinted_id,
                        item.vinted_url,
                        "Sold",
                        item_date_str,
                        now_str,
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
                        "",
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
        Inserts new sold orders into Sales_Log and moves matching Item_Listings to Sold_Items.
        Records Posted_Price, Sold_Price, and Price_Discount in Sales_Log.
        """
        sh = self.client.get_spreadsheet()
        ws_sales = sh.worksheet(TAB_SALES_LOG)
        ws_sold = sh.worksheet(TAB_SOLD_ITEMS)

        sales_rows = ws_sales.get_all_values()
        existing_order_ids = set()
        if len(sales_rows) > 1:
            for row in sales_rows[1:]:
                if row and row[0].strip():
                    existing_order_ids.add(row[0].strip())

        new_sales_rows = []

        for order in scraped_orders:
            if order.order_id.strip() in existing_order_ids:
                continue

            clean_title = order.item_title.strip()
            # 1. Attempt to move matching active listing to Sold_Items with sold_price
            moved_item = self.move_listing_to_sold(
                sku=order.item_sku,
                title=clean_title,
                status="Sold",
                sold_price=order.sold_price,
            )

            matched_sku = ""
            matched_brand = ""
            matched_category = ""
            matched_fav: Any = ""

            if moved_item:
                matched_sku = moved_item.get("Item_SKU", "")
                matched_brand = moved_item.get("Brand", "")
                matched_category = moved_item.get("Category", "")
                matched_fav = moved_item.get("Favorites", "")
            else:
                # 2. Check if already in Sold_Items, and update Sold_Price
                sold_rows = ws_sold.get_all_values()
                if len(sold_rows) > 1:
                    s_headers = sold_rows[0]
                    s_sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
                    s_title_idx = s_headers.index("Title") if "Title" in s_headers else 2
                    s_brand_idx = s_headers.index("Brand") if "Brand" in s_headers else 3
                    s_cat_idx = s_headers.index("Category") if "Category" in s_headers else 4
                    s_fav_idx = s_headers.index("Favorites") if "Favorites" in s_headers else 12
                    s_sold_price_idx = s_headers.index("Sold_Price") if "Sold_Price" in s_headers else 11

                    clean_low = clean_title.lower()
                    for r_i, sr in enumerate(sold_rows[1:], start=2):
                        sr_sku = sr[s_sku_idx].strip().upper() if len(sr) > s_sku_idx else ""
                        sr_title = sr[s_title_idx].strip().lower() if len(sr) > s_title_idx else ""
                        if (order.item_sku and sr_sku == order.item_sku.strip().upper()) or (clean_low and sr_title == clean_low):
                            matched_sku = sr_sku
                            matched_brand = sr[s_brand_idx] if len(sr) > s_brand_idx else ""
                            matched_category = sr[s_cat_idx] if len(sr) > s_cat_idx else ""
                            matched_fav = sr[s_fav_idx] if len(sr) > s_fav_idx else 0
                            if s_sold_price_idx is not None and order.sold_price:
                                col_sp = chr(ord('A') + s_sold_price_idx)
                                ws_sold.update_acell(f"{col_sp}{r_i}", order.sold_price)
                            break

            if not matched_sku:
                matched_sku = order.item_sku or ""
            if not matched_brand:
                matched_brand = order.brand or ""
            if not matched_category:
                matched_category = order.category or ""
            if matched_fav == "":
                matched_fav = order.favorites if order.favorites is not None else ""

            next_row_num = len(sales_rows) + len(new_sales_rows) + 1

            new_sale_data = [
                order.order_id,                                                   # A: Order_ID
                order.platform or "Vinted",                                       # B: Platform
                matched_sku,                                                      # C: Item_SKU
                order.item_title,                                                 # D: Item_Title
                matched_brand or f'=IFERROR(VLOOKUP(C{next_row_num}, Sold_Items!A:E, 4, FALSE), IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:E, 4, FALSE), ""))', # E: Brand
                matched_category or f'=IFERROR(VLOOKUP(C{next_row_num}, Sold_Items!A:E, 5, FALSE), IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:E, 5, FALSE), ""))', # F: Category
                matched_fav if matched_fav != "" else f'=IFERROR(VLOOKUP(C{next_row_num}, Sold_Items!A:M, 13, FALSE), IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:M, 13, FALSE), 0))', # G: Favorites
                order.sale_date.strftime("%Y-%m-%d") if order.sale_date else datetime.now().strftime("%Y-%m-%d"), # H: Sale_Date
                f'=IFERROR(VLOOKUP(C{next_row_num}, Sold_Items!A:G, 7, FALSE), IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:G, 7, FALSE), 0))', # I: Posted_Price
                order.sold_price,                                                 # J: Sold_Price
                f'=IF(AND(ISNUMBER(I{next_row_num}), ISNUMBER(J{next_row_num}), I{next_row_num}>0), I{next_row_num}-J{next_row_num}, 0)', # K: Price_Discount
                order.seller_fees,                                                # L: Seller_Fees
                order.net_payout,                                                 # M: Net_Payout
                f"=IFERROR(VLOOKUP(C{next_row_num}, Sold_Items!A:F, 6, FALSE), IFERROR(VLOOKUP(C{next_row_num}, Item_Listings!A:F, 6, FALSE), 0))", # N: Item_Cost
                f'=IF(B{next_row_num}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)', # O: Packaging_Cost
                f"=M{next_row_num}-N{next_row_num}-O{next_row_num}",             # P: Net_Profit
                f"=IF(J{next_row_num}>0, P{next_row_num}/J{next_row_num}, 0)",  # Q: Margin_%
                order.vinted_status or "Completed",                               # R: Order_Status
            ]
            new_sales_rows.append(new_sale_data)

        if new_sales_rows:
            ws_sales.append_rows(new_sales_rows, value_input_option="USER_ENTERED")

        # Also sweep any listings marked Sold
        self.reconcile_sold_listings()

        # Update Inventory_Lots
        self.sync_lots()

        return {"synced_orders": len(new_sales_rows)}
