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
    TAB_SHIPPING_SUPPLIES,
    SOLD_ITEMS_HEADERS,
)
import re
from src.models import ItemListing, SalesOrder
from src.html_parser import infer_brand, infer_clothing_category


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
        self._ws_cache: Dict[str, Any] = {}

    def get_worksheet(self, tab_name: str) -> Any:
        """Caches worksheet objects to prevent redundant fetch_sheet_metadata network calls."""
        if tab_name not in self._ws_cache:
            sh = self.client.get_spreadsheet()
            self._ws_cache[tab_name] = sh.worksheet(tab_name)
        return self._ws_cache[tab_name]

    def clear_worksheet_cache(self):
        self._ws_cache.clear()

    def get_listings_records(self) -> List[Dict[str, Any]]:
        ws = self.get_worksheet(TAB_ITEM_LISTINGS)
        return ws.get_all_records()

    def get_sold_records(self) -> List[Dict[str, Any]]:
        ws = self.get_worksheet(TAB_SOLD_ITEMS)
        return ws.get_all_records()

    def get_sales_records(self) -> List[Dict[str, Any]]:
        ws = self.get_worksheet(TAB_SALES_LOG)
        return ws.get_all_records()

    def get_date_to_lot_map(self) -> Dict[str, str]:
        """Reads Inventory_Lots to map Purchase_Date (YYYY-MM-DD) -> Lot_ID."""
        ws_lots = self.get_worksheet(TAB_INVENTORY_LOTS)
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

    def get_all_lots(self) -> List[Dict[str, Any]]:
        """Reads Inventory_Lots worksheet and returns all lots as dictionaries."""
        ws_lots = self.get_worksheet(TAB_INVENTORY_LOTS)
        lots_rows = ws_lots.get_all_values()
        if len(lots_rows) <= 1:
            return []

        headers = [h.strip() for h in lots_rows[0]]
        lots: List[Dict[str, Any]] = []
        for r in lots_rows[1:]:
            if not r or not any(r):
                continue
            lot_dict = {}
            for h, val in zip(headers, r):
                lot_dict[h] = str(val).strip() if val is not None else ""
            if lot_dict.get("Lot_ID"):
                lots.append(lot_dict)
        return lots

    def get_next_lot_id(self, cached_lots: Optional[List[Dict[str, Any]]] = None) -> str:
        """Determines the next sequential Lot ID without network calls when cached_lots is provided."""
        lots = cached_lots if cached_lots is not None else self.get_all_lots()
        max_num = 0
        for l in lots:
            lid = l.get("Lot_ID", "")
            m = re.search(r"LOT-(\d+)", lid, re.IGNORECASE)
            if m:
                try:
                    num = int(m.group(1))
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
        return f"LOT-{max_num + 1:03d}"

    def add_inventory_lot(
        self,
        lot_id: str,
        purchase_date: str = "",
        source_vendor: str = "",
        total_cost: float = 0.0,
        total_articles: int = 0,
        status: str = "Active",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Appends a new lot row into Inventory_Lots with automated formulas."""
        ws = self.get_worksheet(TAB_INVENTORY_LOTS)
        rows = ws.get_all_values()
        next_row = len(rows) + 1

        # Check if lot_id already exists to prevent duplicate entries
        if len(rows) > 1:
            hdr = rows[0]
            lid_idx = hdr.index("Lot_ID") if "Lot_ID" in hdr else 0
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) > lid_idx and r[lid_idx].strip().upper() == lot_id.strip().upper():
                    return {"status": "exists", "row": r_idx, "lot_id": lot_id}

        unit_cost_formula = f'=IF(A{next_row}="", "", IF(AND(ISNUMBER(E{next_row}), E{next_row}>0), D{next_row}/E{next_row}, ""))'
        items_proc_formula = f'=IF(A{next_row}="", "", COUNTIF(Item_Listings!B:B, A{next_row}) + COUNTIF(Sold_Items!B:B, A{next_row}))'
        status_formula = (
            f'=IF(A{next_row}="", "", IF(AND(ISNUMBER(E{next_row}), E{next_row}>0, COUNTIF(Sold_Items!B:B, A{next_row})>=E{next_row}), '
            f'"Completed", IF(COUNTIF(Item_Listings!B:B, A{next_row})+COUNTIF(Sold_Items!B:B, A{next_row})>0, '
            f'"Active", "Pending")))'
        )

        row_data = [
            lot_id.strip().upper(),
            purchase_date.strip() if purchase_date else datetime.now().strftime("%Y-%m-%d"),
            source_vendor.strip(),
            total_cost,
            total_articles,
            unit_cost_formula,
            items_proc_formula,
            status_formula,
            notes.strip(),
        ]

        ws.append_row(row_data, value_input_option="USER_ENTERED")
        return {"status": "created", "row": next_row, "lot_id": lot_id.strip().upper()}

    def get_all_supplies(self) -> Dict[str, Any]:
        """Reads Shipping_Supplies and returns active supplies and global average packaging cost."""
        ws_sup = self.get_worksheet(TAB_SHIPPING_SUPPLIES)
        rows = ws_sup.get_all_values()
        if len(rows) <= 1:
            return {"supplies": [], "avg_cost_per_shipment": 0.25, "total_rows": 0}

        headers = [h.strip() for h in rows[0]]
        supplies = []
        for r_idx, r in enumerate(rows[1:], start=2):
            if not r or not any(r):
                continue
            s_dict = {}
            for h, val in zip(headers, r):
                s_dict[h] = str(val).strip() if val is not None else ""
            s_dict["_row"] = r_idx

            has_desc = bool(s_dict.get("Item_Description"))
            has_vendor = bool(s_dict.get("Vendor"))
            has_qty = bool(s_dict.get("Quantity"))
            has_cost = bool(s_dict.get("Total_Cost"))

            if has_desc or has_vendor or has_qty or has_cost:
                supplies.append(s_dict)

        avg_cost_str = rows[1][7] if len(rows) > 1 and len(rows[1]) > 7 else "$0.24"
        avg_cost = parse_price(avg_cost_str) or 0.24
        return {"supplies": supplies, "avg_cost_per_shipment": avg_cost, "total_rows": len(rows)}

    def get_next_supply_id(self, cached_supplies: Optional[List[Dict[str, Any]]] = None) -> str:
        """Finds next supply ID without network calls when cached_supplies is provided."""
        if cached_supplies is not None:
            max_num = 0
            for s in cached_supplies:
                s_id = s.get("Supply_ID", "")
                m = re.search(r"SUP-(\d+)", s_id, re.IGNORECASE)
                if m:
                    try:
                        num = int(m.group(1))
                        if num > max_num:
                            max_num = num
                    except ValueError:
                        pass
            return f"SUP-{max_num + 1:02d}"

        ws_sup = self.get_worksheet(TAB_SHIPPING_SUPPLIES)
        rows = ws_sup.get_all_values()
        if len(rows) <= 1:
            return "SUP-01"

        max_num = 0
        first_empty_id = None
        for r in rows[1:]:
            s_id = r[0].strip() if len(r) > 0 else ""
            m = re.search(r"SUP-(\d+)", s_id, re.IGNORECASE)
            if m:
                try:
                    num = int(m.group(1))
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
            is_empty = not any(c.strip() for c in r[1:6] if c)
            if is_empty and not first_empty_id and s_id:
                first_empty_id = s_id

        if first_empty_id:
            return first_empty_id
        return f"SUP-{max_num + 1:02d}"

    def save_shipping_supply(
        self,
        supply_id: str,
        purchase_date: str = "",
        item_description: str = "",
        vendor: str = "",
        quantity: int = 1,
        total_cost: float = 0.0,
    ) -> Dict[str, Any]:
        """Saves or updates a shipping supply row with formulas preserved."""
        ws_sup = self.get_worksheet(TAB_SHIPPING_SUPPLIES)
        rows = ws_sup.get_all_values()

        target_row = None
        if len(rows) > 1:
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) > 0 and r[0].strip().upper() == supply_id.strip().upper():
                    target_row = r_idx
                    break

        clean_p_date = purchase_date.strip() if purchase_date else datetime.now().strftime("%Y-%m-%d")

        if target_row:
            update_range = f"B{target_row}:F{target_row}"
            values = [[clean_p_date, item_description.strip(), vendor.strip(), quantity, total_cost]]
            ws_sup.update(values=values, range_name=update_range, value_input_option="USER_ENTERED")
            return {"status": "updated", "row": target_row, "supply_id": supply_id}
        else:
            next_row = len(rows) + 1
            unit_cost_formula = f'=IF(AND(ISNUMBER(E{next_row}), E{next_row}>0), F{next_row}/E{next_row}, "")'
            avg_cost_formula = f'=IF(E{next_row}="", "", H$2)'
            row_data = [
                supply_id.strip().upper(),
                clean_p_date,
                item_description.strip(),
                vendor.strip(),
                quantity,
                total_cost,
                unit_cost_formula,
                avg_cost_formula,
            ]
            ws_sup.append_row(row_data, value_input_option="USER_ENTERED")
            return {"status": "created", "row": next_row, "supply_id": supply_id}

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
        ws_lots = self.get_worksheet(TAB_INVENTORY_LOTS)
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)

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

    def get_max_sku_num(self, cached_rows: Optional[List[List[Any]]] = None) -> int:
        """Finds highest SKU number across Item_Listings, Sold_Items, and Sales_Log."""
        max_sku = 1000

        if cached_rows:
            for r in cached_rows:
                if len(r) > 0 and str(r[0]).strip().upper().startswith("SKU-"):
                    try:
                        num = int(str(r[0]).strip().upper().replace("SKU-", ""))
                        if num > max_sku:
                            max_sku = num
                    except ValueError:
                        pass
            return max_sku

        for tab_name in (TAB_ITEM_LISTINGS, TAB_SOLD_ITEMS):
            try:
                ws = self.get_worksheet(tab_name)
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
            ws_sales = self.get_worksheet(TAB_SALES_LOG)
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
        sync_lots_after: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Finds an active listing in Item_Listings by SKU, Vinted ID, or Title,
        moves it to Sold_Items with full sale metadata, profit formulas, and sale type,
        and removes it from Item_Listings.
        """
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)

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
            f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(L{next_sold_row}), G{next_sold_row}>L{next_sold_row}), G{next_sold_row}-L{next_sold_row}, 0))', # M: Price_Discount
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

        if sync_lots_after:
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
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)

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
                    sync_lots_after=False,
                )
                if res:
                    moved_count += 1

        if moved_count > 0:
            try:
                self.sync_lots()
            except Exception:
                pass

        return moved_count

    def clean_blank_rows(self, tab_names: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Scans specified worksheets (defaulting to Item_Listings and Sold_Items)
        and deletes any rows where all cells are empty or where both SKU and Title are missing.
        Deletes in reverse order to preserve correct indices.
        Returns a dict mapping tab name to count of removed rows.
        """
        targets = tab_names or [TAB_ITEM_LISTINGS, TAB_SOLD_ITEMS]
        removed_summary: Dict[str, int] = {}

        for tab_name in targets:
            try:
                ws = self.get_worksheet(tab_name)
                all_rows = ws.get_all_values()
                if len(all_rows) <= 1:
                    removed_summary[tab_name] = 0
                    continue

                headers = all_rows[0]
                sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
                title_idx = headers.index("Title") if "Title" in headers else 2

                blank_row_indices = []
                for r_idx, row in enumerate(all_rows[1:], start=2):
                    # Check if entirely blank
                    is_empty = not any(str(c).strip() for c in row)
                    # Check if missing both SKU and Title
                    sku_val = str(row[sku_idx]).strip() if len(row) > sku_idx else ""
                    title_val = str(row[title_idx]).strip() if len(row) > title_idx else ""
                    if is_empty or (not sku_val and not title_val):
                        blank_row_indices.append(r_idx)

                # Delete in descending order
                deleted_count = 0
                for r_to_del in sorted(blank_row_indices, reverse=True):
                    try:
                        ws.delete_rows(r_to_del)
                        deleted_count += 1
                    except Exception:
                        pass

                removed_summary[tab_name] = deleted_count
            except Exception:
                removed_summary[tab_name] = 0

        return removed_summary

    def sync_listings(self, scraped_items: List[ItemListing], auto_assign_lot: bool = True) -> Dict[str, int]:
        """
        Updates live prices, price drop tracking, and statuses of active listings in Item_Listings.
        When an item sells or has sold status, it moves/appends to Sold_Items.
        Automatically assigns Lot_ID when Date_Listed matches Inventory_Lots purchase date.
        """
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)

        listings_rows = ws_listings.get_all_values()
        sold_rows = ws_sold.get_all_values()

        date_to_lot_id = self.get_date_to_lot_map()
        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        l_headers = listings_rows[0] if listings_rows else LISTINGS_HEADERS
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
        fav_col_idx = l_headers.index("Favorites") if "Favorites" in l_headers else 11
        views_col_idx = l_headers.index("Views") if "Views" in l_headers else 12
        id_col_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 13
        url_col_idx = l_headers.index("Vinted_URL") if "Vinted_URL" in l_headers else 14
        status_col_idx = l_headers.index("Status") if "Status" in l_headers else 15
        date_col_idx = l_headers.index("Date_Listed") if "Date_Listed" in l_headers else 16
        synced_col_idx = l_headers.index("Last_Synced") if "Last_Synced" in l_headers else 17

        s_headers = sold_rows[0] if sold_rows else SOLD_ITEMS_HEADERS
        s_sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
        s_title_idx = s_headers.index("Title") if "Title" in s_headers else 2
        s_brand_idx = s_headers.index("Brand") if "Brand" in s_headers else 3
        s_cat_idx = s_headers.index("Category") if "Category" in s_headers else 4
        s_cost_idx = s_headers.index("Unit_Cost") if "Unit_Cost" in s_headers else 5
        s_posted_idx = s_headers.index("Posted_Price") if "Posted_Price" in s_headers else 6
        s_price_idx = s_headers.index("Current_Price") if "Current_Price" in s_headers else 7
        s_sold_price_idx = s_headers.index("Sold_Price") if "Sold_Price" in s_headers else 11
        s_date_idx = s_headers.index("Sale_Date") if "Sale_Date" in s_headers else 14
        s_fav_idx = s_headers.index("Favorites") if "Favorites" in s_headers else 19
        s_views_idx = s_headers.index("Views") if "Views" in s_headers else 20
        s_order_idx = s_headers.index("Order_ID") if "Order_ID" in s_headers else 21
        s_url_idx = s_headers.index("Vinted_URL") if "Vinted_URL" in s_headers else 22
        s_status_idx = s_headers.index("Status") if "Status" in s_headers else 23

        # Map active listings
        listings_by_vinted_id: Dict[str, int] = {}
        listings_by_title: Dict[str, int] = {}
        if len(listings_rows) > 1:
            for row_idx, row in enumerate(listings_rows[1:], start=2):
                if len(row) > id_col_idx and row[id_col_idx].strip():
                    clean_id = row[id_col_idx].replace(",", "").strip()
                    listings_by_vinted_id[clean_id] = row_idx
                if len(row) > title_col_idx and row[title_col_idx].strip():
                    listings_by_title[row[title_col_idx].strip().lower()] = row_idx

        # Map existing sold items
        sold_by_vinted_id: Dict[str, int] = {}
        sold_by_title: Dict[str, int] = {}
        if len(sold_rows) > 1:
            for row_idx, row in enumerate(sold_rows[1:], start=2):
                if s_url_idx != -1 and len(row) > s_url_idx and row[s_url_idx].strip():
                    from src.html_parser import extract_vinted_id
                    v_id_extracted = extract_vinted_id(row[s_url_idx])
                    if v_id_extracted:
                        sold_by_vinted_id[v_id_extracted] = row_idx
                if len(row) > s_title_idx and row[s_title_idx].strip():
                    sold_by_title[row[s_title_idx].strip().lower()] = row_idx

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
                    col_price = chr(ord('A') + s_price_idx)
                    col_fav = chr(ord('A') + s_fav_idx)
                    col_views = chr(ord('A') + s_views_idx)
                    sold_updates.append({"range": f"{col_price}{in_sold_row}", "values": [[curr_val]]})
                    if item.sold_price is not None:
                        col_sp = chr(ord('A') + s_sold_price_idx)
                        sold_updates.append({"range": f"{col_sp}{in_sold_row}", "values": [[item.sold_price]]})
                    sold_updates.append({"range": f"{col_fav}{in_sold_row}", "values": [[item.favorites]]})
                    sold_updates.append({"range": f"{col_views}{in_sold_row}", "values": [[item.views]]})
                    if item.vinted_url and s_url_idx != -1:
                        col_url = chr(ord('A') + s_url_idx)
                        sold_updates.append({"range": f"{col_url}{in_sold_row}", "values": [[item.vinted_url]]})
                    updated_count += 1
                else:
                    # New sold item never tracked in active listings
                    max_sku_num += 1
                    new_sku = f"SKU-{max_sku_num}"
                    next_sold_row_idx = len(sold_rows) + len(new_sold_rows) + 1
                    assigned_lot = item.lot_id or (date_to_lot_id.get(item_date_str, "") if auto_assign_lot else "")
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
                        f'=IF(OR({col_g}{next_sold_row_idx}="", L{next_sold_row_idx}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row_idx}), ISNUMBER(L{next_sold_row_idx}), {col_g}{next_sold_row_idx}>L{next_sold_row_idx}), {col_g}{next_sold_row_idx}-L{next_sold_row_idx}, 0))',
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
                    if auto_assign_lot and not cur_lot and row_dt in date_to_lot_id:
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
                    assigned_lot = item.lot_id or (date_to_lot_id.get(item_date_str, "") if auto_assign_lot else "")
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
        if auto_assign_lot:
            self.auto_assign_lots_by_date()

        # Update Inventory_Lots: unit costs, items processed, status (Completed when all sold)
        self.sync_lots()

        # Clean any accidental blank rows left behind
        self.clean_blank_rows()

        return {
            "updated": updated_count,
            "created": len(new_active_rows) + len(new_sold_rows),
            "moved_to_sold": moved_to_sold_count,
        }

    def sync_orders(self, scraped_orders: List[SalesOrder], default_lot: str = "", auto_assign_lot: bool = True) -> Dict[str, int]:
        """
        Reconciles scraped completed orders directly with Sold_Items.
        Moves matching Item_Listings to Sold_Items, or appends directly if not currently in active listings.
        """
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        date_to_lot_id = self.get_date_to_lot_map()

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
        batch_new_rows = []
        batch_cell_updates = []
        cur_sold_count = len(sold_rows)

        # Pre-index existing Sold_Items by SKU and Title to avoid repeated lookups
        sold_by_sku: Dict[str, int] = {}
        sold_by_title: Dict[str, int] = {}
        for r_i, sr in enumerate(sold_rows[1:], start=2):
            if len(sr) > sku_idx and sr[sku_idx].strip():
                sold_by_sku[sr[sku_idx].strip().upper()] = r_i
            if len(sr) > title_idx and sr[title_idx].strip():
                sold_by_title[sr[title_idx].strip().lower()] = r_i

        # Pre-index active listings ONCE to avoid repeated get_all_values() in move_listing_to_sold
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        listings_rows = ws_listings.get_all_values()
        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)
        active_sku_to_row: Dict[str, int] = {}
        active_title_to_row: Dict[str, int] = {}
        if len(listings_rows) > 1:
            l_headers = listings_rows[0]
            l_sku_idx = l_headers.index("Item_SKU") if "Item_SKU" in l_headers else 0
            l_title_idx = l_headers.index("Title") if "Title" in l_headers else 2
            for r_idx, lr in enumerate(listings_rows[1:], start=2):
                if len(lr) > l_sku_idx and lr[l_sku_idx].strip():
                    active_sku_to_row[lr[l_sku_idx].strip().upper()] = r_idx
                if len(lr) > l_title_idx and lr[l_title_idx].strip():
                    active_title_to_row[lr[l_title_idx].strip().lower()] = r_idx

        for order in scraped_orders:
            if order.order_id.strip() in existing_order_ids:
                continue

            clean_title = (order.item_title or "").strip()
            if not clean_title or (order.sold_price is not None and order.sold_price <= 0):
                continue

            sale_date_str = order.sale_date.strftime("%Y-%m-%d") if order.sale_date else datetime.now().strftime("%Y-%m-%d")
            clean_low = clean_title.lower()

            # 1. Check if present in active listings FIRST and move it to Sold_Items
            has_active_match = (
                (order.item_sku and order.item_sku.strip().upper() in active_sku_to_row)
                or (clean_low in active_title_to_row)
            )

            if has_active_match:
                moved_item = self.move_listing_to_sold(
                    sku=order.item_sku,
                    title=clean_title,
                    status="Sold",
                    sold_price=order.sold_price,
                    sale_date=sale_date_str,
                    platform=order.platform or "Vinted",
                    order_id=order.order_id,
                    sync_lots_after=False,
                )
                if moved_item:
                    synced_count += 1
                    existing_order_ids.add(order.order_id.strip())
                    if order.item_sku:
                        active_sku_to_row.pop(order.item_sku.strip().upper(), None)
                    active_title_to_row.pop(clean_low, None)
                    continue

            # 2. If not in active listings, check if already in Sold_Items and enrich with Order_ID / sold price
            matched_r_i = None
            if order.item_sku and order.item_sku.strip().upper() in sold_by_sku:
                matched_r_i = sold_by_sku[order.item_sku.strip().upper()]
            elif clean_low in sold_by_title:
                matched_r_i = sold_by_title[clean_low]
            else:
                for st_title, s_row in sold_by_title.items():
                    if clean_low and (clean_low in st_title or st_title in clean_low):
                        matched_r_i = s_row
                        break

            if matched_r_i:
                col_ord = chr(ord('A') + order_id_idx)
                batch_cell_updates.append({"range": f"{col_ord}{matched_r_i}", "values": [[order.order_id]]})
                if order.sold_price and order.sold_price > 0:
                    col_sp = chr(ord('A') + sp_idx)
                    batch_cell_updates.append({"range": f"{col_sp}{matched_r_i}", "values": [[order.sold_price]]})
                synced_count += 1
                existing_order_ids.add(order.order_id.strip())
                continue

            # 3. If neither active listing nor existing sold item found, queue for batch append
            max_sku_num += 1
            new_sku = order.item_sku or f"SKU-{max_sku_num}"
            next_row = cur_sold_count + len(batch_new_rows) + 1
            assigned_lot = default_lot or (date_to_lot_id.get(sale_date_str, "") if auto_assign_lot else "")

            new_sold_data = [
                new_sku,                                                      # A: Item_SKU
                assigned_lot,                                                 # B: Lot_ID
                order.item_title,                                             # C: Title
                order.brand or "",                                            # D: Brand
                order.category or "",                                         # E: Category
                f'=IFERROR(VLOOKUP(B{next_row}, Inventory_Lots!A:F, 6, FALSE), 0)', # F: Unit_Cost
                order.sold_price,                                             # G: Posted_Price
                order.sold_price,                                             # H: Current_Price
                "NO",                                                         # I: Price_Lowered
                0.0,                                                          # J: Price_Drop
                "",                                                           # K: Price_History
                order.sold_price,                                             # L: Sold_Price
                f'=IF(OR(G{next_row}="", L{next_row}=""), "", IF(AND(ISNUMBER(G{next_row}), ISNUMBER(L{next_row}), G{next_row}>L{next_row}), G{next_row}-L{next_row}, 0))', # M: Price_Discount
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
            batch_new_rows.append(new_sold_data)
            synced_count += 1
            existing_order_ids.add(order.order_id.strip())

        if batch_cell_updates:
            ws_sold.batch_update(batch_cell_updates, value_input_option="USER_ENTERED")
        if batch_new_rows:
            ws_sold.append_rows(batch_new_rows, value_input_option="USER_ENTERED")

        # Also sweep any listings marked Sold
        self.reconcile_sold_listings()

        # Update Inventory_Lots
        self.sync_lots()

        return {"synced_orders": synced_count}

    def process_email_batch(self, events: List[Any], default_lot: str = "") -> List[Dict[str, Any]]:
        """
        Batch-processes a list of parsed Vinted email events (sales, offers, favorites)
        with minimal Google Sheets API calls:
        - Single batch read for active listings and sold items.
        - In-memory matching and formula evaluation.
        - Preserves user-assigned lot, title, brand, and category from preview editor.
        - Infers brand and category when missing so records are never blank.
        - Single batch cell updates for listings and single batch append for sold items.
        - Single notification banner update at the end.
        """
        if not events:
            return []

        sh = self.client.get_spreadsheet()
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)

        # 1. Single batch get for both listings and sold items (1 read request!)
        batch_ranges = [f"{TAB_ITEM_LISTINGS}!A:N", f"{TAB_SOLD_ITEMS}!A:Y"]
        try:
            batch_res = sh.values_batch_get(batch_ranges)
            value_ranges = batch_res.get("valueRanges", [])
            listings_rows = value_ranges[0].get("values", []) if len(value_ranges) > 0 else []
            sold_rows = value_ranges[1].get("values", []) if len(value_ranges) > 1 else []
        except Exception:
            listings_rows = ws_listings.get_all_values()
            sold_rows = ws_sold.get_all_values()

        if len(listings_rows) <= 1:
            return [{"status": "no_listings"} for _ in events]

        l_headers = listings_rows[0]
        sku_idx = l_headers.index("Item_SKU") if "Item_SKU" in l_headers else 0
        lot_idx = l_headers.index("Lot_ID") if "Lot_ID" in l_headers else 1
        title_idx = l_headers.index("Title") if "Title" in l_headers else 2
        brand_idx = l_headers.index("Brand") if "Brand" in l_headers else 3
        cat_idx = l_headers.index("Category") if "Category" in l_headers else 4
        cost_idx = l_headers.index("Unit_Cost") if "Unit_Cost" in l_headers else 5
        posted_idx = l_headers.index("Posted_Price") if "Posted_Price" in l_headers else 6
        curr_idx = l_headers.index("Current_Price") if "Current_Price" in l_headers else 7
        hist_idx = l_headers.index("Price_History") if "Price_History" in l_headers else 10
        fav_idx = l_headers.index("Favorites") if "Favorites" in l_headers else 11
        views_idx = l_headers.index("Views") if "Views" in l_headers else 12
        id_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 13
        url_idx = l_headers.index("Vinted_URL") if "Vinted_URL" in l_headers else 14

        s_headers = sold_rows[0] if sold_rows else []
        s_ord_idx = s_headers.index("Order_ID") if "Order_ID" in s_headers else 21
        existing_orders = set()
        for sr in sold_rows[1:]:
            if len(sr) > s_ord_idx and sr[s_ord_idx].strip():
                existing_orders.add(sr[s_ord_idx].strip())

        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)
        cur_sold_count = len(sold_rows)

        # In-memory mapping of active listings: row_idx -> row list
        active_listings_by_row: Dict[int, List[Any]] = {}
        row_by_vinted_id: Dict[str, int] = {}
        row_by_title: Dict[str, int] = {}
        row_by_url: Dict[str, int] = {}

        for r_i, r in enumerate(listings_rows[1:], start=2):
            active_listings_by_row[r_i] = list(r)
            r_id = r[id_idx].replace(",", "").strip() if len(r) > id_idx else ""
            r_title = r[title_idx].strip().lower() if len(r) > title_idx else ""
            r_url = r[url_idx].strip().lower() if len(r) > url_idx else ""
            if r_id:
                row_by_vinted_id[r_id] = r_i
            if r_title:
                row_by_title[r_title] = r_i
            if r_url:
                row_by_url[r_url] = r_i

        listings_cell_updates: List[Dict[str, Any]] = []
        rows_to_append_to_sold: List[List[Any]] = []
        rows_to_delete_from_listings: List[int] = []
        results: List[Dict[str, Any]] = []

        col_fav_letter = chr(ord('A') + fav_idx)
        col_hist_letter = chr(ord('A') + hist_idx)

        for event in events:
            clean_target_title = (event.item_title or "").strip().lower()
            clean_target_id = (event.vinted_id or "").replace(",", "").strip()
            clean_target_url = (event.vinted_url or "").strip().lower()

            target_row = None
            if clean_target_id and clean_target_id in row_by_vinted_id:
                target_row = row_by_vinted_id[clean_target_id]
            elif clean_target_url and clean_target_url in row_by_url:
                target_row = row_by_url[clean_target_url]
            elif clean_target_title:
                if clean_target_title in row_by_title:
                    target_row = row_by_title[clean_target_title]
                else:
                    for t_str, r_idx in row_by_title.items():
                        if clean_target_title in t_str or t_str in clean_target_title:
                            target_row = r_idx
                            break

            # 1. FAVORITE
            if event.event_type == "favorite":
                if target_row and target_row in active_listings_by_row:
                    row_data = active_listings_by_row[target_row]
                    cur_val = row_data[fav_idx] if len(row_data) > fav_idx else "0"
                    try:
                        new_val = int(float(cur_val)) + 1
                    except ValueError:
                        new_val = 1
                    while len(row_data) <= fav_idx:
                        row_data.append("")
                    row_data[fav_idx] = str(new_val)
                    listings_cell_updates.append({"range": f"{col_fav_letter}{target_row}", "values": [[new_val]]})
                    results.append({"status": "favorite_incremented", "row": target_row, "new_count": new_val})
                else:
                    results.append({"status": "item_not_found"})

            # 2. OFFER
            elif event.event_type == "offer":
                if target_row and target_row in active_listings_by_row:
                    row_data = active_listings_by_row[target_row]
                    old_hist = row_data[hist_idx] if len(row_data) > hist_idx else ""
                    today_md = event.timestamp.strftime("%m/%d")
                    offer_str = f"{today_md} Offer: ${event.offer_price:.2f}"
                    if event.listed_price > 0:
                        offer_str += f" (was ${event.listed_price:.2f})"
                    updated_hist = f"{old_hist} | {offer_str}" if str(old_hist).strip() else offer_str
                    while len(row_data) <= hist_idx:
                        row_data.append("")
                    row_data[hist_idx] = updated_hist
                    listings_cell_updates.append({"range": f"{col_hist_letter}{target_row}", "values": [[updated_hist]]})
                    results.append({"status": "offer_logged", "row": target_row, "offer": event.offer_price, "history": updated_hist})
                else:
                    results.append({"status": "item_not_found"})

            # 3. SALE
            elif event.event_type == "sale":
                if event.order_id and event.order_id in existing_orders:
                    results.append({"status": "order_already_synced", "order_id": event.order_id})
                    continue

                sale_date_str = event.timestamp.strftime("%Y-%m-%d")
                next_sold_row = cur_sold_count + len(rows_to_append_to_sold) + 1
                col_lot = "B"
                col_g = "G"
                col_h = "H"

                if target_row and target_row in active_listings_by_row:
                    t_data = active_listings_by_row.pop(target_row)
                    if clean_target_id in row_by_vinted_id:
                        row_by_vinted_id.pop(clean_target_id, None)
                    if clean_target_title in row_by_title:
                        row_by_title.pop(clean_target_title, None)

                    rows_to_delete_from_listings.append(target_row)

                    item_sku = t_data[sku_idx] if len(t_data) > sku_idx and t_data[sku_idx].strip() else f"SKU-{max_sku_num + 1}"
                    lot_id = getattr(event, "lot_id", "") or (t_data[lot_idx] if len(t_data) > lot_idx and t_data[lot_idx].strip() else default_lot)
                    item_title = event.item_title or (t_data[title_idx] if len(t_data) > title_idx else "")
                    brand = getattr(event, "brand", "") or (t_data[brand_idx] if len(t_data) > brand_idx and t_data[brand_idx].strip() else infer_brand(item_title))
                    category = getattr(event, "category", "") or (t_data[cat_idx] if len(t_data) > cat_idx and t_data[cat_idx].strip() else infer_clothing_category(item_title))
                    posted_p = t_data[posted_idx] if len(t_data) > posted_idx else event.sold_price
                    curr_p = t_data[curr_idx] if len(t_data) > curr_idx else event.sold_price
                    hist_p = t_data[hist_idx] if len(t_data) > hist_idx else ""
                    fav_val = t_data[fav_idx] if len(t_data) > fav_idx else 0
                    view_val = t_data[views_idx] if len(t_data) > views_idx else 0
                    v_url = event.vinted_url or (f"https://www.vinted.com/items/{clean_target_id}" if clean_target_id else "")

                    sold_row = [
                        item_sku, lot_id, item_title, brand, category,
                        f'=IFERROR(VLOOKUP({col_lot}{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)',
                        posted_p, curr_p, "NO", 0.0, hist_p, event.sold_price,
                        f'=IF(OR({col_g}{next_sold_row}="", L{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER(L{next_sold_row}), {col_g}{next_sold_row}>L{next_sold_row}), {col_g}{next_sold_row}-L{next_sold_row}, 0))',
                        f'=IF(OR({col_g}{next_sold_row}="", L{next_sold_row}=""), "", IF(L{next_sold_row}>={col_g}{next_sold_row}, "Original Price", IF(L{next_sold_row}={col_h}{next_sold_row}, "Lowered Price", "Accepted Offer")))',
                        sale_date_str, "Vinted",
                        f'=IF(P{next_sold_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
                        f'=IF(AND(ISNUMBER(L{next_sold_row}), ISNUMBER(F{next_sold_row}), ISNUMBER(Q{next_sold_row})), L{next_sold_row}-F{next_sold_row}-Q{next_sold_row}, IF(ISNUMBER(L{next_sold_row}), L{next_sold_row}, 0))',
                        f'=IF(AND(ISNUMBER(L{next_sold_row}), L{next_sold_row}>0), R{next_sold_row}/L{next_sold_row}, 0)',
                        fav_val, view_val, event.order_id, v_url, "Sold", sale_date_str,
                    ]
                    rows_to_append_to_sold.append(sold_row)
                    if event.order_id:
                        existing_orders.add(event.order_id)
                    results.append({"status": "moved_to_sold", "title": item_title, "sku": item_sku, "lot": lot_id})

                else:
                    max_sku_num += 1
                    new_sku = f"SKU-{max_sku_num}"
                    assigned_lot = getattr(event, "lot_id", "") or default_lot
                    assigned_title = event.item_title or (f"Vinted Item {clean_target_id}" if clean_target_id else f"Order {event.order_id}")
                    assigned_brand = getattr(event, "brand", "") or infer_brand(assigned_title)
                    assigned_cat = getattr(event, "category", "") or infer_clothing_category(assigned_title)

                    sold_row = [
                        new_sku, assigned_lot, assigned_title, assigned_brand, assigned_cat,
                        f'=IFERROR(VLOOKUP({col_lot}{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)',
                        event.sold_price, event.sold_price, "NO", 0.0, "", event.sold_price,
                        f'=IF(OR({col_g}{next_sold_row}="", L{next_sold_row}=""), "", IF(AND(ISNUMBER({col_g}{next_sold_row}), ISNUMBER(L{next_sold_row}), {col_g}{next_sold_row}>L{next_sold_row}), {col_g}{next_sold_row}-L{next_sold_row}, 0))',
                        f'=IF(OR({col_g}{next_sold_row}="", L{next_sold_row}=""), "", IF(L{next_sold_row}>={col_g}{next_sold_row}, "Original Price", IF(L{next_sold_row}={col_h}{next_sold_row}, "Lowered Price", "Accepted Offer")))',
                        sale_date_str, "Vinted",
                        f'=IF(P{next_sold_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
                        f'=IF(AND(ISNUMBER(L{next_sold_row}), ISNUMBER(F{next_sold_row}), ISNUMBER(Q{next_sold_row})), L{next_sold_row}-F{next_sold_row}-Q{next_sold_row}, IF(ISNUMBER(L{next_sold_row}), L{next_sold_row}, 0))',
                        f'=IF(AND(ISNUMBER(L{next_sold_row}), L{next_sold_row}>0), R{next_sold_row}/L{next_sold_row}, 0)',
                        0, 0, event.order_id, event.vinted_url, "Sold", sale_date_str,
                    ]
                    rows_to_append_to_sold.append(sold_row)
                    if event.order_id:
                        existing_orders.add(event.order_id)
                    results.append({"status": "appended_to_sold", "title": assigned_title, "sku": new_sku, "lot": assigned_lot})

            else:
                results.append({"status": "ignored"})

        # 4. Flush updates to Google Sheets in single batch operations!
        if listings_cell_updates:
            ws_listings.batch_update(listings_cell_updates, value_input_option="USER_ENTERED")

        if rows_to_append_to_sold:
            ws_sold.append_rows(rows_to_append_to_sold, value_input_option="USER_ENTERED")

        if rows_to_delete_from_listings:
            # Delete in descending order of row index so row indices don't shift
            for d_row in sorted(rows_to_delete_from_listings, reverse=True):
                try:
                    ws_listings.delete_rows(d_row)
                except Exception:
                    pass

        # 5. Record banner update ONCE for the entire batch
        try:
            from src.notifications import record_email_parse_event
            record_email_parse_event(details=f"Email batch: {len(events)} events processed", client=self.client)
        except Exception:
            pass

        # 6. Synchronize inventory lots ONCE
        # 6. Synchronize inventory lots ONCE
        self.sync_lots()

        # 7. Clean accidental blank rows
        self.clean_blank_rows()

        return results

    def process_email_event(self, event) -> Dict[str, Any]:
        """
        Directly reconciles a parsed Vinted email event (sale, offer, or favorite)
        with Google Sheets. Delegates to process_email_batch for single-item safety.
        """
        results = self.process_email_batch([event])
        return results[0] if results else {"status": "ignored"}

    def reconcile_missing_sold_metadata(self, default_lot: str = "") -> Dict[str, int]:
        """
        Scans Sold_Items for rows missing Title, Brand, Category, or Lot_ID.
        Cross-references with Item_Listings, URL slugs, and category inference to backfill missing fields.
        Returns a dict of counts of updated fields.
        """
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)

        sold_rows = ws_sold.get_all_values()
        listings_rows = ws_listings.get_all_values()

        if len(sold_rows) <= 1:
            return {"fixed_rows": 0, "titles_fixed": 0, "lots_assigned": 0, "brands_fixed": 0, "categories_fixed": 0}

        s_headers = sold_rows[0]
        sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
        lot_idx = s_headers.index("Lot_ID") if "Lot_ID" in s_headers else 1
        title_idx = s_headers.index("Title") if "Title" in s_headers else 2
        brand_idx = s_headers.index("Brand") if "Brand" in s_headers else 3
        cat_idx = s_headers.index("Category") if "Category" in s_headers else 4
        url_idx = s_headers.index("Vinted_URL") if "Vinted_URL" in s_headers else 22

        # Index active listings by Vinted ID, URL, and Title
        l_headers = listings_rows[0] if listings_rows else []
        l_title_idx = l_headers.index("Title") if "Title" in l_headers else 2
        l_lot_idx = l_headers.index("Lot_ID") if "Lot_ID" in l_headers else 1
        l_brand_idx = l_headers.index("Brand") if "Brand" in l_headers else 3
        l_cat_idx = l_headers.index("Category") if "Category" in l_headers else 4
        l_id_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 13
        l_url_idx = l_headers.index("Vinted_URL") if "Vinted_URL" in l_headers else 14

        listings_by_id: Dict[str, List[str]] = {}
        listings_by_title: Dict[str, List[str]] = {}
        listings_by_url: Dict[str, List[str]] = {}
        for lr in listings_rows[1:]:
            vid = lr[l_id_idx].replace(",", "").strip() if len(lr) > l_id_idx else ""
            ltit = lr[l_title_idx].strip().lower() if len(lr) > l_title_idx else ""
            lurl = lr[l_url_idx].strip().lower() if len(lr) > l_url_idx else ""
            if vid:
                listings_by_id[vid] = lr
            if ltit:
                listings_by_title[ltit] = lr
            if lurl:
                listings_by_url[lurl] = lr

        updates: List[Dict[str, Any]] = []
        fixed_rows = 0
        titles_fixed = 0
        lots_assigned = 0
        brands_fixed = 0
        categories_fixed = 0

        for r_i, row in enumerate(sold_rows[1:], start=2):
            cur_lot = row[lot_idx].strip() if len(row) > lot_idx else ""
            cur_title = row[title_idx].strip() if len(row) > title_idx else ""
            cur_brand = row[brand_idx].strip() if len(row) > brand_idx else ""
            cur_cat = row[cat_idx].strip() if len(row) > cat_idx else ""
            cur_url = row[url_idx].strip() if len(row) > url_idx else ""

            # Check if this row needs fixing
            needs_title = not cur_title or cur_title.lower().startswith("order ") or cur_title.lower().startswith("vinted item")
            needs_lot = not cur_lot and bool(default_lot)
            needs_brand = not cur_brand or cur_brand.lower() in ("unbranded", "none")
            needs_cat = not cur_cat or cur_cat.lower() in ("other apparel", "none")

            if not (needs_title or needs_lot or needs_brand or needs_cat):
                continue

            # Check for active listing match by URL or ID
            v_id_match = re.search(r"/items/(\d+)", cur_url)
            v_id = v_id_match.group(1) if v_id_match else ""
            matched_listing = listings_by_id.get(v_id) or listings_by_url.get(cur_url.lower()) or (listings_by_title.get(cur_title.lower()) if cur_title else None)

            row_changed = False

            # 1. Title
            if needs_title:
                new_title = ""
                if matched_listing and len(matched_listing) > l_title_idx and matched_listing[l_title_idx].strip():
                    new_title = matched_listing[l_title_idx].strip()
                elif cur_url:
                    slug_match = re.search(r"/items/\d+-([a-zA-Z0-9_-]+)", cur_url)
                    if slug_match:
                        new_title = slug_match.group(1).replace("-", " ").replace("_", " ").title().strip()
                if new_title and new_title != cur_title:
                    cur_title = new_title
                    col_t = chr(ord('A') + title_idx)
                    updates.append({"range": f"{col_t}{r_i}", "values": [[new_title]]})
                    titles_fixed += 1
                    row_changed = True

            # 2. Lot
            if not cur_lot:
                new_lot = ""
                if matched_listing and len(matched_listing) > l_lot_idx and matched_listing[l_lot_idx].strip():
                    new_lot = matched_listing[l_lot_idx].strip()
                elif default_lot:
                    new_lot = default_lot
                if new_lot:
                    cur_lot = new_lot
                    col_l = chr(ord('A') + lot_idx)
                    updates.append({"range": f"{col_l}{r_i}", "values": [[new_lot]]})
                    lots_assigned += 1
                    row_changed = True

            # 3. Brand
            if needs_brand:
                new_brand = ""
                if matched_listing and len(matched_listing) > l_brand_idx and matched_listing[l_brand_idx].strip():
                    new_brand = matched_listing[l_brand_idx].strip()
                elif cur_title:
                    new_brand = infer_brand(cur_title)
                if new_brand and new_brand != cur_brand:
                    col_b = chr(ord('A') + brand_idx)
                    updates.append({"range": f"{col_b}{r_i}", "values": [[new_brand]]})
                    brands_fixed += 1
                    row_changed = True

            # 4. Category
            if needs_cat:
                new_cat = ""
                if matched_listing and len(matched_listing) > l_cat_idx and matched_listing[l_cat_idx].strip():
                    new_cat = matched_listing[l_cat_idx].strip()
                elif cur_title:
                    new_cat = infer_clothing_category(cur_title)
                if new_cat and new_cat != cur_cat:
                    col_c = chr(ord('A') + cat_idx)
                    updates.append({"range": f"{col_c}{r_i}", "values": [[new_cat]]})
                    categories_fixed += 1
                    row_changed = True

            if row_changed:
                fixed_rows += 1

        if updates:
            ws_sold.batch_update(updates, value_input_option="USER_ENTERED")
            self.sync_lots()

        return {
            "fixed_rows": fixed_rows,
            "titles_fixed": titles_fixed,
            "lots_assigned": lots_assigned,
            "brands_fixed": brands_fixed,
            "categories_fixed": categories_fixed,
        }


def reconcile_missing_sold_metadata(engine: SheetsSyncEngine, default_lot: str = "") -> Dict[str, int]:
    """Module-level helper to backfill missing metadata in Sold_Items."""
    return engine.reconcile_missing_sold_metadata(default_lot=default_lot)




