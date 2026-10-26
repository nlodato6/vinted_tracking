import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
import gspread
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import (
    TAB_ITEM_LISTINGS,
    TAB_HIDDEN_ITEMS,
    TAB_SOLD_ITEMS,
    TAB_CANCELLED_ORDERS,
    TAB_SALES_LOG,
    TAB_INVENTORY_LOTS,
    TAB_SHIPPING_SUPPLIES,
    LISTINGS_HEADERS,
    HIDDEN_ITEMS_HEADERS,
    SOLD_ITEMS_HEADERS,
    CANCELLED_ORDERS_HEADERS,
    LOTS_HEADERS,
    SUPPLIES_HEADERS,
    SALES_HEADERS,
)
from src.models import ItemListing, SalesOrder
from src.html_parser import infer_brand, infer_clothing_category

logger = logging.getLogger(__name__)


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


def normalize_date_str(s: Any) -> str:
    """Normalizes various date formats (e.g. 10/19/2026, 2026-10-19) to YYYY-MM-DD."""
    if not s:
        return ""
    cleaned = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return cleaned


def col_letter(idx: int) -> str:
    """Converts 0-based column index to spreadsheet column letter (A, B, ..., Z, AA, AB...)."""
    result = ""
    idx += 1
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def is_placeholder_title(title: Any) -> bool:
    """Returns True if the title is empty or a synthetic fallback / placeholder."""
    if title is None:
        return True
    t = str(title).strip().lower()
    if not t or t in ("untitled", "none", "item", "vinted item", "nan", "null", "blank", "unbranded", "unknown", "order sold", "sold"):
        return True
    if re.match(r"^(order|item|transaction|sale)\b", t):
        return True
    if re.match(r"^vinted\s+item\b", t):
        return True
    if t.startswith("ord-") and any(c.isdigit() for c in t):
        return True
    return False


def is_placeholder_brand(brand: Any) -> bool:
    """Returns True if brand is blank, unknown, or unbranded."""
    if brand is None:
        return True
    b = str(brand).strip().lower()
    return not b or b in ("unbranded", "unknown", "none", "nan", "null", "other", "generic")


def is_placeholder_category(category: Any) -> bool:
    """Returns True if category is blank or generic default."""
    if category is None:
        return True
    c = str(category).strip().lower()
    return not c or c in ("other apparel", "other", "uncategorized", "none", "nan", "null", "unknown")


def is_placeholder_lot(lot: Any) -> bool:
    """Returns True if lot is empty or unassigned."""
    if lot is None:
        return True
    l = str(lot).strip().lower()
    return not l or l in ("none", "⚪ none", "⚪ none (unassigned)", "nan", "null", "unassigned")


def is_placeholder_order_id(order_id: Any) -> bool:
    """Returns True if order_id is empty or a timestamp fallback ORD-... that can be upgraded to a real UUID."""
    if order_id is None:
        return True
    o = str(order_id).strip().lower()
    if not o or o in ("none", "nan", "null"):
        return True
    if o.startswith("ord-") and all(c.isdigit() for c in o[4:]):
        return True
    return False


UUID_REGEX = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def extract_uuid(text: Any) -> Optional[str]:
    """Finds first standard UUID in string, lowercased."""
    if not text:
        return None
    m = UUID_REGEX.search(str(text))
    return m.group(0).lower() if m else None


def extract_vinted_id_from_text(text: Any) -> Optional[str]:
    """Extracts numeric Vinted ID from URL or text."""
    if not text:
        return None
    s = str(text)
    m = re.search(r"/items/(\d+)", s)
    if m:
        return m.group(1)
    m2 = re.search(r"vinted\s+item\s*(?:#|id)?\s*(\d+)", s, re.IGNORECASE)
    if m2:
        return m2.group(1)
    clean_digits = re.sub(r"[^\d]", "", s)
    if len(clean_digits) >= 6 and (s.isdigit() or f"item {clean_digits}" in s.lower()):
        return clean_digits
    return None


class SheetsSyncEngine:
    def __init__(self, client: GoogleSheetsClient):
        self.client = client
        self._ws_cache: Dict[str, Any] = {}

    def find_matching_sold_row(
        self,
        sold_rows: List[List[str]],
        headers: List[str],
        *,
        order_id: str = "",
        vinted_id: str = "",
        vinted_url: str = "",
        item_sku: str = "",
        title: str = "",
        sold_price: Optional[float] = None,
        sale_date: str = "",
        already_matched_indices: Optional[set] = None,
        brand: str = "",
    ) -> Optional[int]:
        """
        Multi-factor duplicate detection to find an existing row in Sold_Items
        before adding a new row.
        Returns the 1-based row index (row number in spreadsheet, >= 2) or None.
        Matches by:
        1. Exact Order_ID
        2. Transaction/Order UUID match (in Order_ID, URL, or Title)
        3. Numeric Vinted Item ID (in URL or Title)
        4. Exact Vinted URL
        5. Item SKU match
        6. Real Title match (neither record is a placeholder, and brands do not differ)
        7. Fuzzy Price + Sale Date match (when existing row or incoming record has a placeholder title, and brands do not differ)
        """
        if len(sold_rows) <= 1:
            return None

        order_id_idx = headers.index("Order_ID") if "Order_ID" in headers else 21
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        title_idx = headers.index("Title") if "Title" in headers else 2
        brand_idx = headers.index("Brand") if "Brand" in headers else 3
        url_idx = headers.index("Vinted_URL") if "Vinted_URL" in headers else 22
        price_idx = headers.index("Sold_Price") if "Sold_Price" in headers else 11
        date_idx = headers.index("Sale_Date") if "Sale_Date" in headers else 14

        already_matched = already_matched_indices or set()

        clean_order_id = str(order_id).strip() if (order_id is not None and str(order_id).strip()) else ""
        clean_incoming_uuid = extract_uuid(order_id) or extract_uuid(vinted_url) or extract_uuid(title)
        clean_incoming_vid = (str(vinted_id).strip() if (vinted_id is not None and str(vinted_id).strip()) else "") or extract_vinted_id_from_text(vinted_url) or extract_vinted_id_from_text(title) or ""
        clean_incoming_url = str(vinted_url).strip().lower() if (vinted_url is not None and str(vinted_url).strip()) else ""
        clean_sku = str(item_sku).strip().upper() if (item_sku is not None and str(item_sku).strip()) else ""
        clean_title = str(title).strip() if (title is not None and str(title).strip()) else ""
        clean_price = round(float(sold_price), 2) if (sold_price is not None and float(sold_price) > 0) else None
        clean_date = normalize_date_str(sale_date) if sale_date else ""
        clean_brand = str(brand).strip().lower() if brand is not None else ""
        eff_incoming_b = clean_brand if not is_placeholder_brand(clean_brand) else (infer_brand(clean_title) or "").strip().lower()

        # --- Tier 1: Exact High-Confidence Identifiers ---
        for r_idx, row in enumerate(sold_rows[1:], start=2):
            if r_idx in already_matched:
                continue

            row_ord = str(row[order_id_idx]).strip() if (len(row) > order_id_idx and row[order_id_idx] is not None) else ""
            row_url = str(row[url_idx]).strip() if (len(row) > url_idx and row[url_idx] is not None) else ""
            row_t = str(row[title_idx]).strip() if (len(row) > title_idx and row[title_idx] is not None) else ""
            row_sku = str(row[sku_idx]).strip() if (len(row) > sku_idx and row[sku_idx] is not None) else ""

            # 1. Exact Order ID
            if clean_order_id and not is_placeholder_order_id(clean_order_id):
                if row_ord and clean_order_id.lower() == row_ord.lower():
                    return r_idx

            # 2. UUID Match
            if clean_incoming_uuid:
                row_uuid = extract_uuid(row_ord) or extract_uuid(row_url) or extract_uuid(row_t)
                if row_uuid and row_uuid == clean_incoming_uuid:
                    return r_idx

            # 3. Numeric Vinted ID
            if clean_incoming_vid:
                row_vid = extract_vinted_id_from_text(row_url) or extract_vinted_id_from_text(row_t)
                if row_vid and row_vid == clean_incoming_vid:
                    return r_idx

            # 4. Exact URL
            if clean_incoming_url and row_url:
                r_url_clean = row_url.strip().lower()
                if clean_incoming_url == r_url_clean or clean_incoming_url.split("?")[0] == r_url_clean.split("?")[0]:
                    return r_idx

            # 5. Exact SKU
            if clean_sku and row_sku and clean_sku == row_sku.strip().upper():
                return r_idx

        # --- Tier 2: Real Title Match (Only if brands do not conflict) ---
        if clean_title and not is_placeholder_title(clean_title):
            c_low = clean_title.lower()
            for r_idx, row in enumerate(sold_rows[1:], start=2):
                if r_idx in already_matched:
                    continue
                row_t = str(row[title_idx]).strip() if (len(row) > title_idx and row[title_idx] is not None) else ""
                if not is_placeholder_title(row_t):
                    r_low = row_t.lower()
                    if c_low == r_low or (len(c_low) >= 10 and (c_low in r_low or r_low in c_low)):
                        row_b = str(row[brand_idx]).strip().lower() if (brand_idx != -1 and len(row) > brand_idx and row[brand_idx] is not None) else ""
                        eff_row_b = row_b if not is_placeholder_brand(row_b) else (infer_brand(row_t) or "").strip().lower()
                        if (
                            not is_placeholder_brand(eff_incoming_b)
                            and not is_placeholder_brand(eff_row_b)
                            and eff_incoming_b != eff_row_b
                        ):
                            continue
                        return r_idx

        # --- Tier 3: Price + Sale Date Match for Incomplete / Placeholder Rows ---
        if clean_price is not None and clean_price > 0:
            for r_idx, row in enumerate(sold_rows[1:], start=2):
                if r_idx in already_matched:
                    continue
                row_t = str(row[title_idx]).strip() if (len(row) > title_idx and row[title_idx] is not None) else ""
                if is_placeholder_title(row_t) or is_placeholder_title(clean_title):
                    row_b = str(row[brand_idx]).strip().lower() if (brand_idx != -1 and len(row) > brand_idx and row[brand_idx] is not None) else ""
                    eff_row_b = row_b if not is_placeholder_brand(row_b) else (infer_brand(row_t) or "").strip().lower()
                    if (
                        not is_placeholder_brand(eff_incoming_b)
                        and not is_placeholder_brand(eff_row_b)
                        and eff_incoming_b != eff_row_b
                    ):
                        continue
                    row_p = parse_price(row[price_idx]) if len(row) > price_idx else 0.0
                    if abs(clean_price - row_p) < 0.01:
                        row_d = normalize_date_str(row[date_idx]) if len(row) > date_idx else ""
                        if clean_date and row_d:
                            try:
                                d1 = datetime.strptime(clean_date, "%Y-%m-%d")
                                d2 = datetime.strptime(row_d, "%Y-%m-%d")
                                if abs((d1 - d2).days) <= 2:
                                    return r_idx
                            except Exception:
                                if clean_date == row_d:
                                    return r_idx
                        elif not clean_date or not row_d:
                            return r_idx

        return None

    def enrich_sold_row(
        self,
        existing_row: List[str],
        headers: List[str],
        row_idx: int,
        incoming: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Compares existing row in Sold_Items against incoming data and generates
        cell updates for any missing or placeholder fields. Updates existing_row in-place.
        """
        updates: List[Dict[str, Any]] = []

        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        lot_idx = headers.index("Lot_ID") if "Lot_ID" in headers else 1
        title_idx = headers.index("Title") if "Title" in headers else 2
        brand_idx = headers.index("Brand") if "Brand" in headers else 3
        cat_idx = headers.index("Category") if "Category" in headers else 4
        cost_idx = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
        posted_idx = headers.index("Posted_Price") if "Posted_Price" in headers else 6
        curr_idx = headers.index("Current_Price") if "Current_Price" in headers else 7
        sp_idx = headers.index("Sold_Price") if "Sold_Price" in headers else 11
        disc_idx = headers.index("Price_Discount") if "Price_Discount" in headers else 12
        stype_idx = headers.index("Sale_Type") if "Sale_Type" in headers else 13
        date_idx = headers.index("Sale_Date") if "Sale_Date" in headers else 14
        plat_idx = headers.index("Platform") if "Platform" in headers else 15
        pack_idx = headers.index("Packaging_Cost") if "Packaging_Cost" in headers else 16
        net_idx = headers.index("Net_Profit") if "Net_Profit" in headers else 17
        margin_idx = headers.index("Margin_%") if "Margin_%" in headers else 18
        fav_idx = headers.index("Favorites") if "Favorites" in headers else 19
        views_idx = headers.index("Views") if "Views" in headers else 20
        ord_idx = headers.index("Order_ID") if "Order_ID" in headers else 21
        url_idx = headers.index("Vinted_URL") if "Vinted_URL" in headers else 22
        status_idx = headers.index("Status") if "Status" in headers else 23
        dl_idx = headers.index("Date_Listed") if "Date_Listed" in headers else 24
        source_idx = headers.index("Source") if "Source" in headers else 25

        while len(existing_row) <= 25:
            existing_row.append("")
        for idx in range(len(existing_row)):
            if existing_row[idx] is None:
                existing_row[idx] = ""
            elif not isinstance(existing_row[idx], str):
                existing_row[idx] = str(existing_row[idx])

        # 1. Title
        existing_title = (existing_row[title_idx] or "").strip()
        new_title = str(incoming.get("item_title") or "").strip()
        if new_title and not is_placeholder_title(new_title):
            if is_placeholder_title(existing_title) or not existing_title:
                updates.append({"range": f"{col_letter(title_idx)}{row_idx}", "values": [[new_title]]})
                existing_row[title_idx] = new_title

        effective_title = existing_row[title_idx] if not is_placeholder_title(existing_row[title_idx]) else new_title

        # 2. Brand
        existing_brand = (existing_row[brand_idx] or "").strip()
        new_brand = str(incoming.get("brand") or "").strip()
        if is_placeholder_brand(new_brand):
            new_brand = infer_brand(effective_title)
        if not is_placeholder_brand(new_brand) and is_placeholder_brand(existing_brand):
            updates.append({"range": f"{col_letter(brand_idx)}{row_idx}", "values": [[new_brand]]})
            existing_row[brand_idx] = new_brand

        # 3. Category
        existing_cat = (existing_row[cat_idx] or "").strip()
        new_cat = str(incoming.get("category") or "").strip()
        if is_placeholder_category(new_cat):
            new_cat = infer_clothing_category(effective_title)
        if not is_placeholder_category(new_cat) and is_placeholder_category(existing_cat):
            updates.append({"range": f"{col_letter(cat_idx)}{row_idx}", "values": [[new_cat]]})
            existing_row[cat_idx] = new_cat

        # 4. Lot_ID
        existing_lot = (existing_row[lot_idx] or "").strip()
        new_lot = str(incoming.get("lot_id") or "").strip()
        if not is_placeholder_lot(new_lot) and is_placeholder_lot(existing_lot):
            updates.append({"range": f"{col_letter(lot_idx)}{row_idx}", "values": [[new_lot]]})
            existing_row[lot_idx] = new_lot
            col_b = col_letter(lot_idx)
            cost_formula = f'=IFERROR(VLOOKUP({col_b}{row_idx}, Inventory_Lots!A:F, 6, FALSE), 0)'
            updates.append({"range": f"{col_letter(cost_idx)}{row_idx}", "values": [[cost_formula]]})
            existing_row[cost_idx] = cost_formula

        # 5. Order_ID
        existing_ord = (existing_row[ord_idx] or "").strip() if ord_idx != -1 else ""
        new_ord = str(incoming.get("order_id") or "").strip()
        if new_ord and (not existing_ord or (is_placeholder_order_id(existing_ord) and not is_placeholder_order_id(new_ord))):
            updates.append({"range": f"{col_letter(ord_idx)}{row_idx}", "values": [[new_ord]]})
            existing_row[ord_idx] = new_ord

        # 6. Vinted_URL
        existing_url = (existing_row[url_idx] or "").strip() if url_idx != -1 else ""
        new_url = str(incoming.get("vinted_url") or "").strip()
        if new_url and (not existing_url or (len(new_url) > len(existing_url) and not existing_url.startswith("http"))):
            updates.append({"range": f"{col_letter(url_idx)}{row_idx}", "values": [[new_url]]})
            existing_row[url_idx] = new_url

        # 7. Sold_Price & Prices
        existing_sp = parse_price(existing_row[sp_idx])
        new_sp = incoming.get("sold_price")
        if new_sp is not None and float(new_sp) > 0:
            if existing_sp <= 0:
                updates.append({"range": f"{col_letter(sp_idx)}{row_idx}", "values": [[float(new_sp)]]})
                existing_row[sp_idx] = str(new_sp)
            if not parse_price(existing_row[posted_idx]):
                updates.append({"range": f"{col_letter(posted_idx)}{row_idx}", "values": [[float(new_sp)]]})
                existing_row[posted_idx] = str(new_sp)
            if not parse_price(existing_row[curr_idx]):
                updates.append({"range": f"{col_letter(curr_idx)}{row_idx}", "values": [[float(new_sp)]]})
                existing_row[curr_idx] = str(new_sp)

        # 8. Favorites & Views
        new_fav = int(incoming.get("favorites") or 0)
        existing_fav = int(existing_row[fav_idx]) if (str(existing_row[fav_idx]).isdigit()) else 0
        if new_fav > existing_fav:
            updates.append({"range": f"{col_letter(fav_idx)}{row_idx}", "values": [[new_fav]]})
            existing_row[fav_idx] = str(new_fav)

        new_views = int(incoming.get("views") or 0)
        existing_views = int(existing_row[views_idx]) if (str(existing_row[views_idx]).isdigit()) else 0
        if new_views > existing_views:
            updates.append({"range": f"{col_letter(views_idx)}{row_idx}", "values": [[new_views]]})
            existing_row[views_idx] = str(new_views)

        # 9. Date_Listed & Sale_Date
        new_dl = normalize_date_str(incoming.get("date_listed") or "")
        existing_dl = (existing_row[dl_idx] or "").strip() if dl_idx != -1 else ""
        if new_dl and not existing_dl:
            updates.append({"range": f"{col_letter(dl_idx)}{row_idx}", "values": [[new_dl]]})
            existing_row[dl_idx] = new_dl

        new_sd = normalize_date_str(incoming.get("sale_date") or "")
        existing_sd = (existing_row[date_idx] or "").strip() if date_idx != -1 else ""
        if new_sd and not existing_sd:
            updates.append({"range": f"{col_letter(date_idx)}{row_idx}", "values": [[new_sd]]})
            existing_row[date_idx] = new_sd

        # 10. Status
        existing_st = (existing_row[status_idx] or "").strip() if status_idx != -1 else ""
        if existing_st.lower() != "sold":
            updates.append({"range": f"{col_letter(status_idx)}{row_idx}", "values": [["Sold"]]})
            existing_row[status_idx] = "Sold"

        # 11. Formulas check (Unit_Cost, Price_Discount, Sale_Type, Packaging_Cost, Net_Profit, Margin_%)
        col_b = col_letter(lot_idx)
        col_f = col_letter(cost_idx)
        col_g = col_letter(posted_idx)
        col_h = col_letter(curr_idx)
        col_l = col_letter(sp_idx)
        col_m = col_letter(disc_idx)
        col_n = col_letter(stype_idx)
        col_p = col_letter(plat_idx)
        col_q = col_letter(pack_idx)
        col_r = col_letter(net_idx)
        col_s = col_letter(margin_idx)

        if not str(existing_row[cost_idx]).startswith("="):
            c_f = f'=IFERROR(VLOOKUP({col_b}{row_idx}, Inventory_Lots!A:F, 6, FALSE), 0)'
            updates.append({"range": f"{col_f}{row_idx}", "values": [[c_f]]})
            existing_row[cost_idx] = c_f

        if not str(existing_row[disc_idx]).startswith("="):
            m_f = f'=IF(OR({col_g}{row_idx}="", {col_l}{row_idx}=""), "", IF(AND(ISNUMBER({col_g}{row_idx}), ISNUMBER({col_l}{row_idx}), {col_g}{row_idx}>{col_l}{row_idx}), {col_g}{row_idx}-{col_l}{row_idx}, 0))'
            updates.append({"range": f"{col_m}{row_idx}", "values": [[m_f]]})
            existing_row[disc_idx] = m_f

        if not str(existing_row[stype_idx]).startswith("="):
            n_f = f'=IF(OR({col_g}{row_idx}="", {col_l}{row_idx}=""), "", IF({col_l}{row_idx}>={col_g}{row_idx}, "Original Price", IF({col_l}{row_idx}={col_h}{row_idx}, "Lowered Price", "Accepted Offer")))'
            updates.append({"range": f"{col_n}{row_idx}", "values": [[n_f]]})
            existing_row[stype_idx] = n_f

        if not str(existing_row[pack_idx]).startswith("="):
            q_f = f'=IF({col_p}{row_idx}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)'
            updates.append({"range": f"{col_q}{row_idx}", "values": [[q_f]]})
            existing_row[pack_idx] = q_f

        if not str(existing_row[net_idx]).startswith("="):
            r_f = f'=IF(AND(ISNUMBER({col_l}{row_idx}), ISNUMBER({col_f}{row_idx}), ISNUMBER({col_q}{row_idx})), {col_l}{row_idx}-{col_f}{row_idx}-{col_q}{row_idx}, IF(ISNUMBER({col_l}{row_idx}), {col_l}{row_idx}, 0))'
            updates.append({"range": f"{col_r}{row_idx}", "values": [[r_f]]})
            existing_row[net_idx] = r_f

        if not str(existing_row[margin_idx]).startswith("="):
            s_f = f'=IF(AND(ISNUMBER({col_l}{row_idx}), {col_l}{row_idx}>0), {col_r}{row_idx}/{col_l}{row_idx}, 0)'
            updates.append({"range": f"{col_s}{row_idx}", "values": [[s_f]]})
            existing_row[margin_idx] = s_f

        # 12. Source (HTML, Email, or Email + HTML)
        existing_source = (existing_row[source_idx] or "").strip() if source_idx != -1 and len(existing_row) > source_idx else ""
        incoming_source = str(incoming.get("source") or "").strip()
        if incoming_source:
            if not existing_source:
                merged_source = incoming_source
            elif existing_source == incoming_source:
                merged_source = existing_source
            elif ("Email" in existing_source and "HTML" in incoming_source) or ("HTML" in existing_source and "Email" in incoming_source):
                merged_source = "Email + HTML"
            else:
                merged_source = existing_source

            if merged_source and merged_source != existing_source:
                updates.append({"range": f"{col_letter(source_idx)}{row_idx}", "values": [[merged_source]]})
                existing_row[source_idx] = merged_source

        return updates

    def _ensure_headers(self, ws: Any, expected_headers: List[str]):
        """Ensures that the worksheet header row contains all expected headers (including newly added columns like 'Source')."""
        try:
            rows = ws.get_all_values()
            if not rows:
                ws.update(range_name="A1", values=[expected_headers])
                return
            current_headers = rows[0]
            if len(current_headers) < len(expected_headers):
                from src.sheets.templates import ensure_col_count, format_header_row
                try:
                    ensure_col_count(ws, len(expected_headers))
                except Exception:
                    pass
                for idx in range(len(current_headers), len(expected_headers)):
                    col_l = col_letter(idx)
                    ws.update(range_name=f"{col_l}1", values=[[expected_headers[idx]]])
                try:
                    format_header_row(ws, len(expected_headers))
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Could not auto-ensure headers on worksheet: {e}")

    def get_worksheet(self, tab_name: str) -> Any:
        """Caches worksheet objects to prevent redundant fetch_sheet_metadata network calls.
        If the worksheet does not exist in the spreadsheet (e.g. an existing sheet created before
        Cancelled_Orders or Hidden_Items tabs were introduced), it automatically creates the worksheet
        with schema headers and formatting.
        """
        if tab_name not in self._ws_cache:
            sh = self.client.get_spreadsheet()
            try:
                self._ws_cache[tab_name] = sh.worksheet(tab_name)
            except Exception as e:
                headers_map = {
                    TAB_INVENTORY_LOTS: (LOTS_HEADERS, 200),
                    TAB_ITEM_LISTINGS: (LISTINGS_HEADERS, 500),
                    TAB_HIDDEN_ITEMS: (HIDDEN_ITEMS_HEADERS, 500),
                    TAB_SOLD_ITEMS: (SOLD_ITEMS_HEADERS, 500),
                    TAB_CANCELLED_ORDERS: (CANCELLED_ORDERS_HEADERS, 300),
                    TAB_SHIPPING_SUPPLIES: (SUPPLIES_HEADERS, 200),
                    TAB_SALES_LOG: (SALES_HEADERS, 500),
                }
                if tab_name in headers_map and hasattr(sh, "add_worksheet"):
                    headers, num_rows = headers_map[tab_name]
                    try:
                        new_ws = sh.add_worksheet(title=tab_name, rows=num_rows, cols=max(len(headers), 15))
                        new_ws.update(range_name="A1", values=[headers])
                        try:
                            from src.sheets.templates import format_header_row, safe_batch_format
                            format_header_row(new_ws, len(headers))
                            if tab_name == TAB_CANCELLED_ORDERS:
                                safe_batch_format(new_ws, [
                                    {"range": "F2:G300", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
                                    {"range": "H2:H300", "format": {"horizontalAlignment": "CENTER"}},
                                    {"range": "I2:I300", "format": {"horizontalAlignment": "CENTER"}},
                                    {"range": "L2:L300", "format": {"horizontalAlignment": "CENTER"}},
                                ])
                            elif tab_name == TAB_HIDDEN_ITEMS:
                                safe_batch_format(new_ws, [
                                    {"range": "F2:H500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
                                    {"range": "I2:I500", "format": {"horizontalAlignment": "CENTER"}},
                                    {"range": "J2:J500", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
                                    {"range": "L2:M500", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                                    {"range": "P2:P500", "format": {"horizontalAlignment": "CENTER"}},
                                    {"range": "S2:S500", "format": {"horizontalAlignment": "CENTER"}},
                                ])
                        except Exception:
                            pass
                        self._ws_cache[tab_name] = new_ws
                    except Exception as create_err:
                        logger.error(f"Failed to auto-create missing worksheet '{tab_name}': {create_err}")
                        raise e
                else:
                    raise e
        return self._ws_cache[tab_name]

    def ensure_required_tabs(self) -> List[str]:
        """
        Verifies that all required worksheets exist in the Google Spreadsheet.
        If any (such as Cancelled_Orders or Hidden_Items) are missing, creates them
        immediately with schema headers and proper formatting.
        Returns a list of tab names that were newly created.
        """
        sh = self.client.get_spreadsheet()
        try:
            if hasattr(sh, "worksheets"):
                existing_tabs = {ws.title for ws in sh.worksheets()}
            elif hasattr(sh, "worksheets_dict"):
                existing_tabs = set(sh.worksheets_dict.keys())
            else:
                existing_tabs = set()
        except Exception:
            existing_tabs = set()

        created = []
        for tab_name in [
            TAB_ITEM_LISTINGS,
            TAB_SOLD_ITEMS,
            TAB_INVENTORY_LOTS,
            TAB_CANCELLED_ORDERS,
            TAB_HIDDEN_ITEMS,
            TAB_SHIPPING_SUPPLIES,
        ]:
            if tab_name not in existing_tabs:
                try:
                    self.get_worksheet(tab_name)
                    created.append(tab_name)
                except Exception as e:
                    logger.warning(f"Could not provision tab {tab_name}: {e}")
        return created

    def wipe_and_reset_sheet(self) -> Dict[str, Any]:
        """
        Completely resets Item_Listings, Sold_Items, Hidden_Items, and Cancelled_Orders
        to 0 data rows with clean schema headers and template formatting.
        Resets Inventory_Lots to LOT-001 with 0 items processed.
        Re-applies all schema formatting and dashboard formulas.
        """
        from src.sheets.templates import initialize_all_tabs, format_header_row

        results = {}
        tabs_to_reset = [
            (TAB_ITEM_LISTINGS, LISTINGS_HEADERS),
            (TAB_SOLD_ITEMS, SOLD_ITEMS_HEADERS),
            (TAB_HIDDEN_ITEMS, HIDDEN_ITEMS_HEADERS),
            (TAB_CANCELLED_ORDERS, CANCELLED_ORDERS_HEADERS),
        ]

        for tab_name, headers in tabs_to_reset:
            try:
                ws = self.get_worksheet(tab_name)
                ws.clear()
                ws.update(range_name="A1", values=[headers])
                try:
                    format_header_row(ws, len(headers))
                except Exception:
                    pass
                results[tab_name] = "wiped"
            except Exception as e:
                results[tab_name] = f"error: {e}"

        # Reset Inventory_Lots
        try:
            ws_lots = self.get_worksheet(TAB_INVENTORY_LOTS)
            ws_lots.clear()
            ws_lots.update(range_name="A1", values=[LOTS_HEADERS])
            try:
                format_header_row(ws_lots, len(LOTS_HEADERS))
            except Exception:
                pass
            lot_001_row = [
                "LOT-001",
                "2026-10-01",
                "Consolidated Hauls (Initial)",
                199.00,
                351,
                '=IF(A2="", "", IF(AND(ISNUMBER(E2), E2>0), D2/E2, ""))',
                '=IF(A2="", "", COUNTIF(Item_Listings!B:B, A2) + COUNTIF(Sold_Items!B:B, A2))',
                '=IF(A2="", "", IF(AND(ISNUMBER(E2), E2>0, COUNTIF(Sold_Items!B:B, A2)>=E2), "Completed", IF(COUNTIF(Item_Listings!B:B, A2)+COUNTIF(Sold_Items!B:B, A2)>0, "Active", "Pending")))',
                "Baseline lot covering all initial inventory",
            ]
            ws_lots.update(range_name="A2", values=[lot_001_row], value_input_option="USER_ENTERED")
            results[TAB_INVENTORY_LOTS] = "reset"
        except Exception as e:
            results[TAB_INVENTORY_LOTS] = f"error: {e}"

        try:
            initialize_all_tabs(self.client)
        except Exception as e:
            logger.warning(f"Error re-initializing all tabs: {e}")

        self.clear_worksheet_cache()
        return results

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
                if len(lr) > l_lot_idx and lr[l_lot_idx] is not None and str(lr[l_lot_idx]).strip():
                    l_id = str(lr[l_lot_idx]).strip()
                    active_counts[l_id] = active_counts.get(l_id, 0) + 1

        sold_counts: Dict[str, int] = {}
        if len(sold_rows) > 1:
            s_lot_idx = sold_rows[0].index("Lot_ID") if "Lot_ID" in sold_rows[0] else 1
            for sr in sold_rows[1:]:
                if len(sr) > s_lot_idx and sr[s_lot_idx] is not None and str(sr[s_lot_idx]).strip():
                    s_id = str(sr[s_lot_idx]).strip()
                    sold_counts[s_id] = sold_counts.get(s_id, 0) + 1

        updates = []
        completed_count = 0
        active_lot_count = 0

        col_unit_cost = chr(ord('A') + unit_cost_idx)
        col_proc = chr(ord('A') + proc_idx)
        col_status = chr(ord('A') + status_idx)

        for row_idx, r in enumerate(lots_rows[1:], start=2):
            lot_id = str(r[lot_id_idx]).strip() if (len(r) > lot_id_idx and r[lot_id_idx] is not None) else ""
            if not lot_id:
                continue

            total_articles_str = str(r[total_art_idx]).strip() if (len(r) > total_art_idx and r[total_art_idx] is not None) else ""
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

            cur_cost = str(r[unit_cost_idx]).strip() if (len(r) > unit_cost_idx and r[unit_cost_idx] is not None) else ""
            cur_proc = str(r[proc_idx]).strip() if (len(r) > proc_idx and r[proc_idx] is not None) else ""
            cur_status = str(r[status_idx]).strip() if (len(r) > status_idx and r[status_idx] is not None) else ""

            if not cur_cost.startswith("="):
                updates.append({"range": f"{col_unit_cost}{row_idx}", "values": [[expected_cost_formula]]})
            if not cur_proc.startswith("="):
                updates.append({"range": f"{col_proc}{row_idx}", "values": [[expected_proc_formula]]})
            if not cur_status.startswith("="):
                updates.append({"range": f"{col_status}{row_idx}", "values": [[expected_status_formula]]})

        if updates:
            ws_lots.batch_update(updates, value_input_option="USER_ENTERED")

        return {
            "total_lots": len([r for r in lots_rows[1:] if len(r) > lot_id_idx and r[lot_id_idx] is not None and str(r[lot_id_idx]).strip()]),
            "completed_lots": completed_count,
            "active_lots": active_lot_count,
            "updated_cells": len(updates),
        }

    def get_max_sku_num(self, cached_rows: Optional[List[List[Any]]] = None) -> int:
        """Finds highest SKU number across Item_Listings, Sold_Items, and Sales_Log."""
        max_sku = 1000

        if cached_rows:
            for r in cached_rows:
                if len(r) > 0 and r[0] is not None and str(r[0]).strip().upper().startswith("SKU-"):
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
                        if len(r) > sku_idx and r[sku_idx] is not None and str(r[sku_idx]).strip().upper().startswith("SKU-"):
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

        l_source_idx = headers.index("Source") if "Source" in headers else 18
        origin_source = (target_row_data[l_source_idx] if len(target_row_data) > l_source_idx and target_row_data[l_source_idx] else "").strip()
        if ord_ref.lower().startswith("ord-"):
            final_source = "Email + HTML" if ("HTML" in origin_source or not origin_source) else "Email"
        else:
            final_source = origin_source or "HTML"

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
            final_source,                                                     # Z: Source
        ]

        s_headers = sold_rows[0] if sold_rows else SOLD_ITEMS_HEADERS
        # Check if item is already in Sold_Items (e.g. from email sync) before appending duplicate
        matched_sold_idx = self.find_matching_sold_row(
            sold_rows=sold_rows,
            headers=s_headers,
            order_id=ord_ref,
            vinted_id=clean_id,
            vinted_url=v_url,
            item_sku=clean_sku,
            title=item_title,
            sold_price=sp_val,
            sale_date=s_date_str,
            brand=brand,
        )
        if matched_sold_idx:
            enrich_updates = self.enrich_sold_row(
                existing_row=sold_rows[matched_sold_idx - 1],
                headers=s_headers,
                row_idx=matched_sold_idx,
                incoming={
                    "item_title": item_title,
                    "brand": brand,
                    "category": category,
                    "lot_id": lot_id,
                    "order_id": ord_ref,
                    "vinted_url": v_url,
                    "sold_price": sp_val,
                    "current_price": curr_price,
                    "posted_price": posted_price,
                    "favorites": favs,
                    "views": views,
                    "date_listed": dt_listed,
                    "sale_date": s_date_str,
                    "source": final_source,
                }
            )
            if enrich_updates:
                ws_sold.batch_update(enrich_updates, value_input_option="USER_ENTERED")
            ws_listings.delete_rows(target_row_idx)
            if sync_lots_after:
                try:
                    self.sync_lots()
                except Exception:
                    pass
            return {s_headers[i]: (sold_rows[matched_sold_idx - 1][i] if i < len(sold_rows[matched_sold_idx - 1]) else "") for i in range(len(s_headers))}

        ws_sold.append_row(sold_row_data, value_input_option="USER_ENTERED")
        ws_listings.delete_rows(target_row_idx)

        if sync_lots_after:
            try:
                self.sync_lots()
            except Exception:
                pass

        return {SOLD_ITEMS_HEADERS[i]: sold_row_data[i] for i in range(len(SOLD_ITEMS_HEADERS))}

    def move_listing_to_hidden(
        self,
        sku: Optional[str] = None,
        vinted_id: Optional[str] = None,
        title: Optional[str] = None,
        sync_lots_after: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Finds an active listing in Item_Listings by SKU, Vinted ID, or Title,
        moves it to Hidden_Items with listing metadata, and removes it from Item_Listings.
        """
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_hidden = self.get_worksheet(TAB_HIDDEN_ITEMS)

        self._ensure_headers(ws_listings, LISTINGS_HEADERS)
        self._ensure_headers(ws_hidden, HIDDEN_ITEMS_HEADERS)

        listings_rows = ws_listings.get_all_values()
        if len(listings_rows) <= 1:
            return None

        headers = listings_rows[0]
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        title_idx = headers.index("Title") if "Title" in headers else 2
        id_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else 13

        clean_sku = sku.strip().upper() if sku else ""
        clean_id = vinted_id.strip().replace(",", "") if vinted_id else ""
        clean_title = title.strip().lower() if title else ""

        target_row_idx = None
        target_row_data = None

        for r_idx, r in enumerate(listings_rows[1:], start=2):
            r_sku = r[sku_idx].strip().upper() if len(r) > sku_idx else ""
            r_id = r[id_idx].strip().replace(",", "") if len(r) > id_idx else ""
            r_title = r[title_idx].strip().lower() if len(r) > title_idx else ""

            if (clean_sku and r_sku == clean_sku) or \
               (clean_id and r_id == clean_id) or \
               (clean_title and r_title == clean_title):
                target_row_idx = r_idx
                target_row_data = list(r)
                break

        if not target_row_idx or not target_row_data:
            return None

        hidden_rows = ws_hidden.get_all_values()
        next_hidden_row = len(hidden_rows) + 1

        lot_id = target_row_data[headers.index("Lot_ID")] if "Lot_ID" in headers and len(target_row_data) > headers.index("Lot_ID") else ""
        item_title = target_row_data[title_idx] if len(target_row_data) > title_idx else ""
        brand = target_row_data[headers.index("Brand")] if "Brand" in headers and len(target_row_data) > headers.index("Brand") else ""
        category = target_row_data[headers.index("Category")] if "Category" in headers and len(target_row_data) > headers.index("Category") else ""
        posted_price = target_row_data[headers.index("Posted_Price")] if "Posted_Price" in headers and len(target_row_data) > headers.index("Posted_Price") else ""
        curr_price = target_row_data[headers.index("Current_Price")] if "Current_Price" in headers and len(target_row_data) > headers.index("Current_Price") else ""
        price_hist = target_row_data[headers.index("Price_History")] if "Price_History" in headers and len(target_row_data) > headers.index("Price_History") else ""
        favs = target_row_data[headers.index("Favorites")] if "Favorites" in headers and len(target_row_data) > headers.index("Favorites") else 0
        views = target_row_data[headers.index("Views")] if "Views" in headers and len(target_row_data) > headers.index("Views") else 0
        v_id_val = target_row_data[id_idx] if len(target_row_data) > id_idx else ""
        v_url = target_row_data[headers.index("Vinted_URL")] if "Vinted_URL" in headers and len(target_row_data) > headers.index("Vinted_URL") else ""
        dt_listed = target_row_data[headers.index("Date_Listed")] if "Date_Listed" in headers and len(target_row_data) > headers.index("Date_Listed") else ""
        l_source_idx = headers.index("Source") if "Source" in headers else 18
        source_val = target_row_data[l_source_idx] if len(target_row_data) > l_source_idx else "HTML"

        hidden_row_data = [
            target_row_data[sku_idx],                                                # A: Item_SKU
            lot_id,                                                                  # B: Lot_ID
            item_title,                                                              # C: Title
            brand,                                                                   # D: Brand
            category,                                                                # E: Category
            f'=IFERROR(VLOOKUP(B{next_hidden_row}, Inventory_Lots!A:F, 6, FALSE), 0)', # F: Unit_Cost
            posted_price,                                                            # G: Posted_Price
            curr_price,                                                              # H: Current_Price
            f'=IF(OR(G{next_hidden_row}="", H{next_hidden_row}=""), "", IF(AND(ISNUMBER(G{next_hidden_row}), ISNUMBER(H{next_hidden_row}), H{next_hidden_row}<G{next_hidden_row}), "YES", "NO"))', # I: Price_Lowered
            f'=IF(OR(G{next_hidden_row}="", H{next_hidden_row}=""), "", IF(AND(ISNUMBER(G{next_hidden_row}), ISNUMBER(H{next_hidden_row}), H{next_hidden_row}<G{next_hidden_row}), G{next_hidden_row}-H{next_hidden_row}, 0))', # J: Price_Drop
            price_hist,                                                              # K: Price_History
            favs,                                                                    # L: Favorites
            views,                                                                   # M: Views
            v_id_val,                                                                # N: Vinted_ID
            v_url,                                                                   # O: Vinted_URL
            "Hidden",                                                                # P: Status
            dt_listed,                                                               # Q: Date_Listed
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),                            # R: Last_Synced
            source_val,                                                              # S: Source
        ]

        already_in_hidden = False
        for hr in hidden_rows[1:]:
            h_sku = hr[0].strip().upper() if len(hr) > 0 else ""
            h_vid = hr[13].strip().replace(",", "") if len(hr) > 13 else ""
            h_title = hr[2].strip().lower() if len(hr) > 2 else ""
            if (clean_sku and h_sku == clean_sku) or \
               (clean_id and h_vid == clean_id) or \
               (clean_title and h_title == clean_title):
                already_in_hidden = True
                break

        if not already_in_hidden:
            ws_hidden.append_row(hidden_row_data, value_input_option="USER_ENTERED")

        ws_listings.delete_rows(target_row_idx)

        if sync_lots_after:
            try:
                self.sync_lots()
            except Exception:
                pass

        return {HIDDEN_ITEMS_HEADERS[i]: hidden_row_data[i] for i in range(len(HIDDEN_ITEMS_HEADERS))}

    def append_cancelled_orders(self, cancelled_orders: List[SalesOrder]) -> int:
        """
        Appends cancelled orders to the Cancelled_Orders worksheet.
        Deduplicates by Order_ID so repeat syncs do not duplicate records.
        """
        if not cancelled_orders:
            return 0
        ws = self.get_worksheet(TAB_CANCELLED_ORDERS)
        self._ensure_headers(ws, CANCELLED_ORDERS_HEADERS)
        existing = ws.get_all_values()
        existing_order_ids = {r[0].strip() for r in existing[1:] if len(r) > 0 and r[0].strip()}

        rows_to_add = []
        for o in cancelled_orders:
            if o.order_id and o.order_id in existing_order_ids:
                continue
            sale_date_str = o.sale_date.strftime("%Y-%m-%d") if o.sale_date else datetime.now().strftime("%Y-%m-%d")
            row = [
                o.order_id,
                o.item_sku or "",
                o.item_title,
                o.brand or "",
                o.category or "",
                o.sold_price,
                o.sold_price,
                sale_date_str,
                getattr(o, "cancellation_reason", "") or "Order cancelled. Refund processed.",
                getattr(o, "buyer_username", "") or "",
                getattr(o, "vinted_url", "") or "",
                getattr(o, "source", "HTML") or "HTML",
            ]
            rows_to_add.append(row)
            if o.order_id:
                existing_order_ids.add(o.order_id)

        if rows_to_add:
            ws.append_rows(rows_to_add, value_input_option="USER_ENTERED")
        return len(rows_to_add)

    def reconcile_active_closet_snapshot(
        self,
        active_items: List[ItemListing],
        default_lot: str = "",
        auto_assign_lot: bool = True,
    ) -> Dict[str, Any]:
        """
        Reconciles the uploaded active closet HTML export with Item_Listings:
        1. Identifies listings currently in Item_Listings that are not present in active_items:
           - Items that are known sold (found in Sold_Items, Orders, or marked Sold) are moved to Sold_Items.
           - Items that are hidden (marked Hidden, swimsuits, or inactive) are moved to Hidden_Items.
        2. Updates existing active listings with current prices, views, and favorites.
        """
        active_items = [it for it in active_items if getattr(it, "status", "Listed") != "Sold"]
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        ws_hidden = self.get_worksheet(TAB_HIDDEN_ITEMS)

        self._ensure_headers(ws_listings, LISTINGS_HEADERS)
        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)
        self._ensure_headers(ws_hidden, HIDDEN_ITEMS_HEADERS)

        listings_rows = ws_listings.get_all_values()
        sold_rows = ws_sold.get_all_values()
        hidden_rows = ws_hidden.get_all_values()

        l_headers = listings_rows[0] if listings_rows else LISTINGS_HEADERS
        sku_idx = l_headers.index("Item_SKU") if "Item_SKU" in l_headers else 0
        lot_idx = l_headers.index("Lot_ID") if "Lot_ID" in l_headers else 1
        title_idx = l_headers.index("Title") if "Title" in l_headers else 2
        brand_idx = l_headers.index("Brand") if "Brand" in l_headers else 3
        cat_idx = l_headers.index("Category") if "Category" in l_headers else 4
        posted_idx = l_headers.index("Posted_Price") if "Posted_Price" in l_headers else 6
        curr_idx = l_headers.index("Current_Price") if "Current_Price" in l_headers else 7
        hist_idx = l_headers.index("Price_History") if "Price_History" in l_headers else 10
        fav_idx = l_headers.index("Favorites") if "Favorites" in l_headers else 11
        views_idx = l_headers.index("Views") if "Views" in l_headers else 12
        id_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 13
        url_idx = l_headers.index("Vinted_URL") if "Vinted_URL" in l_headers else 14
        status_idx = l_headers.index("Status") if "Status" in l_headers else 15
        date_idx = l_headers.index("Date_Listed") if "Date_Listed" in l_headers else 16
        synced_col_idx = l_headers.index("Last_Synced") if "Last_Synced" in l_headers else 17
        source_idx = l_headers.index("Source") if "Source" in l_headers else 18

        snapshot_by_vid: Dict[str, ItemListing] = {}
        snapshot_by_title: Dict[str, List[ItemListing]] = {}
        for item in active_items:
            v_id = item.vinted_id.strip().replace(",", "") if item.vinted_id else ""
            if v_id:
                snapshot_by_vid[v_id] = item
            if item.title:
                t = item.title.strip().lower()
                snapshot_by_title.setdefault(t, []).append(item)

        known_sold_titles = set()
        known_sold_vids = set()
        for sr in sold_rows[1:]:
            if len(sr) > 2 and sr[2].strip():
                known_sold_titles.add(sr[2].strip().lower())
            if len(sr) > 22 and sr[22].strip():
                m_vid = re.search(r"/items/(\d+)", sr[22].strip())
                if m_vid:
                    known_sold_vids.add(m_vid.group(1))

        matched_snapshot_vids = set()
        matched_item_objects = set()

        kept_active_rows = []
        rows_to_sold = []
        rows_to_hidden = []
        updated_count = 0

        for r in listings_rows[1:]:
            r_vid = r[id_idx].strip().replace(",", "") if len(r) > id_idx else ""
            r_title = r[title_idx].strip().lower() if len(r) > title_idx else ""
            r_status = r[status_idx].strip() if len(r) > status_idx else ""
            r_url = r[url_idx].strip() if len(r) > url_idx else ""
            r_vid_from_url = ""
            if r_url:
                m_url = re.search(r"/items/(\d+)", r_url)
                if m_url:
                    r_vid_from_url = m_url.group(1)

            matched_item = None
            if r_vid:
                if r_vid in snapshot_by_vid:
                    matched_item = snapshot_by_vid[r_vid]
            elif r_title and r_title in snapshot_by_title:
                for cand in snapshot_by_title[r_title]:
                    cand_vid = cand.vinted_id.strip().replace(",", "") if cand.vinted_id else ""
                    if cand_vid and cand_vid not in matched_snapshot_vids:
                        matched_item = cand
                        break
                    elif not cand_vid and id(cand) not in matched_item_objects:
                        matched_item = cand
                        break

            if matched_item:
                if matched_item.vinted_id:
                    matched_snapshot_vids.add(matched_item.vinted_id.strip().replace(",", ""))
                matched_item_objects.add(id(matched_item))

                row_copy = list(r)
                if matched_item.current_price > 0 and len(row_copy) > curr_idx:
                    row_copy[curr_idx] = matched_item.current_price
                if matched_item.favorites is not None and len(row_copy) > fav_idx:
                    row_copy[fav_idx] = matched_item.favorites
                if matched_item.views is not None and len(row_copy) > views_idx:
                    row_copy[views_idx] = matched_item.views
                if len(row_copy) > status_idx:
                    row_copy[status_idx] = "Listed"
                if len(row_copy) > synced_col_idx:
                    row_copy[synced_col_idx] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                kept_active_rows.append(row_copy)
                updated_count += 1
            else:
                is_swimsuit_or_hidden = (
                    "hidden" in r_status.lower()
                    or "swimsuit" in r_title
                    or "bikini" in r_title
                    or "bean’s clothes" in r_title
                    or "bean's clothes" in r_title
                )
                is_already_sold = (
                    r_title in known_sold_titles
                    or (r_vid and r_vid in known_sold_vids)
                    or (r_vid_from_url and r_vid_from_url in known_sold_vids)
                )
                if is_swimsuit_or_hidden:
                    rows_to_hidden.append(r)
                elif is_already_sold:
                    # Already recorded in Sold_Items; drop from listings without duplicating into Sold_Items
                    pass
                else:
                    rows_to_sold.append(r)
                    if r_title:
                        known_sold_titles.add(r_title)
                    if r_vid:
                        known_sold_vids.add(r_vid)
                    if r_vid_from_url:
                        known_sold_vids.add(r_vid_from_url)

        date_to_lot_id = self.get_date_to_lot_map()
        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)
        new_active_added = 0

        for item in active_items:
            if getattr(item, "status", "Listed") == "Sold":
                continue
            v_id = item.vinted_id.strip().replace(",", "") if item.vinted_id else ""
            if (v_id and v_id in matched_snapshot_vids) or (id(item) in matched_item_objects):
                continue

            max_sku_num += 1
            new_sku = f"SKU-{max_sku_num}"
            assigned_lot = item.lot_id or default_lot or (date_to_lot_id.get(item.date_listed.strftime("%Y-%m-%d"), "") if (auto_assign_lot and item.date_listed) else "")
            item_date_str = item.date_listed.strftime("%Y-%m-%d") if item.date_listed else datetime.now().strftime("%Y-%m-%d")

            posted_val = item.posted_price if (item.posted_price and item.posted_price > 0) else item.current_price
            curr_val = item.current_price

            new_row = [
                new_sku,
                assigned_lot,
                item.title,
                item.brand or "",
                item.category or "",
                f"=IFERROR(VLOOKUP(B{len(kept_active_rows) + 2}, Inventory_Lots!A:F, 6, FALSE), 0)",
                posted_val,
                curr_val,
                f'=IF(OR(G{len(kept_active_rows) + 2}="", H{len(kept_active_rows) + 2}=""), "", IF(AND(ISNUMBER(G{len(kept_active_rows) + 2}), ISNUMBER(H{len(kept_active_rows) + 2}), H{len(kept_active_rows) + 2}<G{len(kept_active_rows) + 2}), "YES", "NO"))',
                f'=IF(OR(G{len(kept_active_rows) + 2}="", H{len(kept_active_rows) + 2}=""), "", IF(AND(ISNUMBER(G{len(kept_active_rows) + 2}), ISNUMBER(H{len(kept_active_rows) + 2}), H{len(kept_active_rows) + 2}<G{len(kept_active_rows) + 2}), G{len(kept_active_rows) + 2}-H{len(kept_active_rows) + 2}, 0))',
                "",
                item.favorites or 0,
                item.views or 0,
                item.vinted_id or "",
                item.vinted_url or "",
                "Listed",
                item_date_str,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                getattr(item, "source", "HTML") or "HTML",
            ]
            kept_active_rows.append(new_row)
            new_active_added += 1
            if v_id:
                matched_snapshot_vids.add(v_id)
            matched_item_objects.add(id(item))

        final_listings_matrix = []
        for idx, row in enumerate(kept_active_rows, start=2):
            r_c = list(row)
            while len(r_c) < len(LISTINGS_HEADERS):
                r_c.append("")
            r_c[5] = f"=IFERROR(VLOOKUP(B{idx}, Inventory_Lots!A:F, 6, FALSE), 0)"
            r_c[8] = f'=IF(OR(G{idx}="", H{idx}=""), "", IF(AND(ISNUMBER(G{idx}), ISNUMBER(H{idx}), H{idx}<G{idx}), "YES", "NO"))'
            r_c[9] = f'=IF(OR(G{idx}="", H{idx}=""), "", IF(AND(ISNUMBER(G{idx}), ISNUMBER(H{idx}), H{idx}<G{idx}), G{idx}-H{idx}, 0))'
            final_listings_matrix.append(r_c)

        # 1. Update Item_Listings in 1 single batch write
        ws_listings.clear()
        ws_listings.update("A1", [LISTINGS_HEADERS] + final_listings_matrix, value_input_option="USER_ENTERED")

        # 2. Append moved items to Sold_Items
        sold_appended = 0
        if rows_to_sold:
            sold_batch_rows = []
            cur_sold_total = len(sold_rows)
            for r in rows_to_sold:
                next_sold_row = cur_sold_total + len(sold_batch_rows) + 1
                r_sku = r[sku_idx] if len(r) > sku_idx else ""
                r_lot = r[lot_idx] if len(r) > lot_idx else default_lot
                r_title = r[title_idx] if len(r) > title_idx else ""
                r_brand = r[brand_idx] if len(r) > brand_idx else ""
                r_cat = r[cat_idx] if len(r) > cat_idx else ""
                r_post = r[posted_idx] if len(r) > posted_idx else ""
                r_curr = r[curr_idx] if len(r) > curr_idx else ""
                r_fav = r[fav_idx] if len(r) > fav_idx else 0
                r_view = r[views_idx] if len(r) > views_idx else 0
                r_url = r[url_idx] if len(r) > url_idx else ""
                r_dt = r[date_idx] if len(r) > date_idx else datetime.now().strftime("%Y-%m-%d")
                r_src = r[source_idx] if len(r) > source_idx else "HTML"

                sp_val = parse_price(r_curr) or parse_price(r_post)

                sold_row_data = [
                    r_sku,                                                           # A: Item_SKU
                    r_lot,                                                           # B: Lot_ID
                    r_title,                                                         # C: Title
                    r_brand,                                                         # D: Brand
                    r_cat,                                                           # E: Category
                    f"=IFERROR(VLOOKUP(B{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)", # F: Unit_Cost
                    r_post,                                                          # G: Posted_Price
                    r_curr,                                                          # H: Current_Price
                    f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), "YES", "NO"))', # I: Price_Lowered
                    f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), G{next_sold_row}-H{next_sold_row}, 0))', # J: Price_Drop
                    "",                                                              # K: Price_History
                    sp_val,                                                          # L: Sold_Price
                    f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(L{next_sold_row}), G{next_sold_row}>L{next_sold_row}), G{next_sold_row}-L{next_sold_row}, 0))', # M: Price_Discount
                    f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(L{next_sold_row}>=G{next_sold_row}, "Original Price", IF(L{next_sold_row}=H{next_sold_row}, "Lowered Price", "Accepted Offer")))', # N: Sale_Type
                    datetime.now().strftime("%Y-%m-%d"),                             # O: Sale_Date
                    "Vinted",                                                        # P: Platform
                    f'=IF(P{next_sold_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)', # Q: Packaging_Cost
                    f'=IF(AND(ISNUMBER(L{next_sold_row}), ISNUMBER(F{next_sold_row}), ISNUMBER(Q{next_sold_row})), L{next_sold_row}-F{next_sold_row}-Q{next_sold_row}, IF(ISNUMBER(L{next_sold_row}), L{next_sold_row}, 0))', # R: Net_Profit
                    f'=IF(AND(ISNUMBER(L{next_sold_row}), L{next_sold_row}>0), R{next_sold_row}/L{next_sold_row}, 0)', # S: Margin_%
                    r_fav,                                                           # T: Favorites
                    r_view,                                                          # U: Views
                    "",                                                              # V: Order_ID
                    r_url,                                                           # W: Vinted_URL
                    "Sold",                                                          # X: Status
                    r_dt,                                                            # Y: Date_Listed
                    r_src,                                                           # Z: Source
                ]
                sold_batch_rows.append(sold_row_data)

            if sold_batch_rows:
                ws_sold.append_rows(sold_batch_rows, value_input_option="USER_ENTERED")
                sold_appended = len(sold_batch_rows)

        # 3. Append moved items to Hidden_Items
        hidden_appended = 0
        if rows_to_hidden:
            hidden_batch_rows = []
            cur_hidden_total = len(hidden_rows)
            for r in rows_to_hidden:
                next_h_row = cur_hidden_total + len(hidden_batch_rows) + 1
                r_c = list(r)
                while len(r_c) < len(HIDDEN_ITEMS_HEADERS):
                    r_c.append("")
                r_c[5] = f"=IFERROR(VLOOKUP(B{next_h_row}, Inventory_Lots!A:F, 6, FALSE), 0)"
                r_c[8] = f'=IF(OR(G{next_h_row}="", H{next_h_row}=""), "", IF(AND(ISNUMBER(G{next_h_row}), ISNUMBER(H{next_h_row}), H{next_h_row}<G{next_h_row}), "YES", "NO"))'
                r_c[9] = f'=IF(OR(G{next_h_row}="", H{next_h_row}=""), "", IF(AND(ISNUMBER(G{next_h_row}), ISNUMBER(H{next_h_row}), H{next_h_row}<G{next_h_row}), G{next_h_row}-H{next_h_row}, 0))'
                r_c[15] = "Hidden"
                hidden_batch_rows.append(r_c)

            if hidden_batch_rows:
                ws_hidden.append_rows(hidden_batch_rows, value_input_option="USER_ENTERED")
                hidden_appended = len(hidden_batch_rows)

        # 4. Sync inventory lots
        try:
            self.sync_lots()
        except Exception:
            pass

        return {
            "final_active_count": len(final_listings_matrix),
            "updated_active": updated_count,
            "new_active_added": new_active_added,
            "moved_to_sold": sold_appended,
            "moved_to_hidden": hidden_appended,
        }

    # ------------------------------------------------------------------
    # Plan-based HTML import (see src/sheets/reconcile.py)
    # ------------------------------------------------------------------
    def read_sheet_state(self) -> "SheetState":
        """Reads Item_Listings, Sold_Items, Hidden_Items and Cancelled_Orders in a single batch request."""
        from src.sheets.reconcile import SheetState

        tabs = [TAB_ITEM_LISTINGS, TAB_SOLD_ITEMS, TAB_HIDDEN_ITEMS, TAB_CANCELLED_ORDERS]
        rows_by_tab: Dict[str, List[List[str]]] = {}
        sh = self.client.get_spreadsheet()
        try:
            res = sh.values_batch_get([f"'{t}'!A:Z" for t in tabs])
            for t, vr in zip(tabs, res.get("valueRanges", [])):
                rows_by_tab[t] = vr.get("values", [])
        except Exception:
            for t in tabs:
                rows_by_tab[t] = self.get_worksheet(t).get_all_values()
        return SheetState(
            listings=rows_by_tab.get(TAB_ITEM_LISTINGS, [])[1:],
            sold=rows_by_tab.get(TAB_SOLD_ITEMS, [])[1:],
            hidden=rows_by_tab.get(TAB_HIDDEN_ITEMS, [])[1:],
            cancelled=rows_by_tab.get(TAB_CANCELLED_ORDERS, [])[1:],
        )

    def apply_sync_plan(self, plan: "SyncPlan", state: "SheetState") -> Dict[str, int]:
        """Writes a SyncPlan using batched operations (about one write per changed tab)."""
        from src.sheets.reconcile import listing_formulas, sold_formulas, with_formulas

        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        ws_hidden = self.get_worksheet(TAB_HIDDEN_ITEMS)
        ws_cancelled = self.get_worksheet(TAB_CANCELLED_ORDERS)

        # 1. Item_Listings: full rewrite (rows move out frequently, formulas are row-relative)
        if plan.listings_changed:
            body = [with_formulas(r, i, listing_formulas) for i, r in enumerate(plan.final_listings, start=2)]
            ws_listings.clear()
            ws_listings.update(range_name="A1", values=[LISTINGS_HEADERS] + body, value_input_option="USER_ENTERED")

        # 2. Sold_Items: enrich cells, delete cancelled rows (bottom-up), append new rows
        if plan.sold_cell_updates:
            ws_sold.batch_update(
                [{"range": f"{col_letter(c)}{r}", "values": [[v]]} for r, c, v in plan.sold_cell_updates],
                value_input_option="USER_ENTERED",
            )
        for r in plan.sold_delete_rows:
            ws_sold.delete_rows(r)
        if plan.sold_appends:
            start = len(state.sold) + 2 - len(plan.sold_delete_rows)
            ws_sold.append_rows(
                [with_formulas(r, start + k, sold_formulas) for k, r in enumerate(plan.sold_appends)],
                value_input_option="USER_ENTERED",
            )

        # 3. Hidden_Items
        for r in plan.hidden_delete_rows:
            ws_hidden.delete_rows(r)
        if plan.hidden_appends:
            start = len(state.hidden) + 2 - len(plan.hidden_delete_rows)
            ws_hidden.append_rows(
                [with_formulas(r, start + k, listing_formulas) for k, r in enumerate(plan.hidden_appends)],
                value_input_option="USER_ENTERED",
            )

        # 4. Cancelled_Orders
        if plan.cancelled_cell_updates:
            ws_cancelled.batch_update(
                [{"range": f"{col_letter(c)}{r}", "values": [[v]]} for r, c, v in plan.cancelled_cell_updates],
                value_input_option="USER_ENTERED",
            )
        if plan.cancelled_appends:
            ws_cancelled.append_rows(plan.cancelled_appends, value_input_option="USER_ENTERED")

        if plan.has_changes:
            try:
                self.sync_lots()
            except Exception as e:
                logger.warning(f"sync_lots after HTML import failed: {e}")
        return dict(plan.counts_after)

    def reconcile_sold_closet(
        self,
        sold_items: List[ItemListing],
        default_lot: str = "",
        auto_assign_lot: bool = True,
    ) -> Dict[str, int]:
        """
        Reconciles items extracted from a Sold Closet HTML export:
        1. If an item in sold_items matches an active listing in Item_Listings (by Vinted ID or Title),
           moves it from Item_Listings to Sold_Items.
        2. If an item in sold_items matches an existing row in Sold_Items, enriches it with missing
           metadata (Vinted URL, favorites, views, brand, category, posted price).
        3. If an item in sold_items is neither in active listings nor in Sold_Items, appends it
           directly to Sold_Items as a new sold record with formulas and assigned lot.
        4. Never appends sold items to Item_Listings.
        """
        if not sold_items:
            return {"moved_to_sold": 0, "new_sold_added": 0, "enriched": 0}

        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        self._ensure_headers(ws_listings, LISTINGS_HEADERS)
        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)
        date_to_lot_id = self.get_date_to_lot_map()

        listings_rows = ws_listings.get_all_values()
        sold_rows = ws_sold.get_all_values()

        active_by_vid: Dict[str, str] = {}
        active_by_title: Dict[str, str] = {}
        if len(listings_rows) > 1:
            l_headers = listings_rows[0]
            l_sku_idx = l_headers.index("Item_SKU") if "Item_SKU" in l_headers else 0
            l_title_idx = l_headers.index("Title") if "Title" in l_headers else 2
            l_id_idx = l_headers.index("Vinted_ID") if "Vinted_ID" in l_headers else 13

            for r in listings_rows[1:]:
                sku = r[l_sku_idx].strip() if len(r) > l_sku_idx else ""
                vid = r[l_id_idx].strip().replace(",", "") if len(r) > l_id_idx else ""
                title = r[l_title_idx].strip().lower() if len(r) > l_title_idx else ""
                if vid and sku:
                    active_by_vid[vid] = sku
                if title and sku:
                    active_by_title[title] = sku

        s_headers = sold_rows[0] if sold_rows else SOLD_ITEMS_HEADERS
        cur_sold_total = len(sold_rows)
        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)

        moved_count = 0
        enriched_count = 0
        batch_new_sold_rows: List[List[Any]] = []
        batch_cell_updates: List[Dict[str, Any]] = []

        seen_new_vids: set = set()
        seen_new_titles: set = set()
        matched_sold_rows: set = set()

        for item in sold_items:
            vid = item.vinted_id.strip().replace(",", "") if item.vinted_id else ""
            title = item.title.strip() if item.title else ""
            clean_low = title.lower()
            if not title:
                continue

            # Deduplicate against items already queued in this exact batch
            if vid and vid in seen_new_vids:
                continue
            if not vid and clean_low in seen_new_titles:
                continue

            # 1. Check if present in active listings FIRST and move it to Sold_Items
            match_sku = None
            if vid and vid in active_by_vid:
                match_sku = active_by_vid[vid]
            elif clean_low and clean_low in active_by_title:
                match_sku = active_by_title[clean_low]

            if match_sku:
                res = self.move_listing_to_sold(
                    sku=match_sku,
                    status="Sold",
                    sold_price=item.current_price,
                    platform="Vinted",
                    sync_lots_after=False,
                )
                if res:
                    moved_count += 1
                    if vid:
                        active_by_vid.pop(vid, None)
                        seen_new_vids.add(vid)
                    if clean_low:
                        active_by_title.pop(clean_low, None)
                        seen_new_titles.add(clean_low)
                    continue

            # 2. Check for duplicate in Sold_Items before appending (enrich existing row if found)
            item_date_str = item.date_listed.strftime("%Y-%m-%d") if item.date_listed else datetime.now().strftime("%Y-%m-%d")
            matched_r_i = self.find_matching_sold_row(
                sold_rows=sold_rows,
                headers=s_headers,
                vinted_id=vid,
                vinted_url=item.vinted_url or "",
                title=title,
                sold_price=item.current_price,
                sale_date=item_date_str,
                already_matched_indices=matched_sold_rows,
                brand=item.brand or "",
            )

            if matched_r_i:
                assigned_lot = item.lot_id or default_lot or (date_to_lot_id.get(item_date_str, "") if auto_assign_lot else "")
                enrich_updates = self.enrich_sold_row(
                    existing_row=sold_rows[matched_r_i - 1],
                    headers=s_headers,
                    row_idx=matched_r_i,
                    incoming={
                        "item_title": title,
                        "brand": item.brand,
                        "category": item.category,
                        "lot_id": assigned_lot,
                        "vinted_url": item.vinted_url,
                        "sold_price": item.current_price,
                        "favorites": item.favorites,
                        "views": item.views,
                        "date_listed": item_date_str,
                        "source": getattr(item, "source", "HTML") or "HTML",
                    },
                )
                if enrich_updates:
                    batch_cell_updates.extend(enrich_updates)
                    enriched_count += 1
                matched_sold_rows.add(matched_r_i)
                if vid:
                    seen_new_vids.add(vid)
                if clean_low:
                    seen_new_titles.add(clean_low)
                continue

            # 3. If neither active listing nor existing sold item found, queue for batch append to Sold_Items
            max_sku_num += 1
            new_sku = item.item_sku or f"SKU-{max_sku_num}"
            next_sold_row = cur_sold_total + len(batch_new_sold_rows) + 1
            assigned_lot = item.lot_id or default_lot or (date_to_lot_id.get(item_date_str, "") if auto_assign_lot else "") or "LOT-001"

            posted_val = item.posted_price if (item.posted_price and item.posted_price > 0) else item.current_price
            curr_val = item.current_price
            sp_val = item.current_price
            item_brand = item.brand or infer_brand(title, "")
            item_cat = item.category or infer_clothing_category(title)
            final_url = item.vinted_url or (f"https://www.vinted.com/items/{vid}" if vid else "")

            sold_row_data = [
                new_sku,                                                                     # A: Item_SKU
                assigned_lot,                                                                # B: Lot_ID
                title,                                                                       # C: Title
                item_brand,                                                                  # D: Brand
                item_cat,                                                                    # E: Category
                f"=IFERROR(VLOOKUP(B{next_sold_row}, Inventory_Lots!A:F, 6, FALSE), 0)",      # F: Unit_Cost
                posted_val,                                                                  # G: Posted_Price
                curr_val,                                                                    # H: Current_Price
                f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), "YES", "NO"))', # I: Price_Lowered
                f'=IF(OR(G{next_sold_row}="", H{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(H{next_sold_row}), H{next_sold_row}<G{next_sold_row}), G{next_sold_row}-H{next_sold_row}, 0))', # J: Price_Drop
                "",                                                                          # K: Price_History
                sp_val,                                                                      # L: Sold_Price
                f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(AND(ISNUMBER(G{next_sold_row}), ISNUMBER(L{next_sold_row}), G{next_sold_row}>L{next_sold_row}), G{next_sold_row}-L{next_sold_row}, 0))', # M: Price_Discount
                f'=IF(OR(G{next_sold_row}="", L{next_sold_row}=""), "", IF(L{next_sold_row}>=G{next_sold_row}, "Original Price", IF(L{next_sold_row}=H{next_sold_row}, "Lowered Price", "Accepted Offer")))', # N: Sale_Type
                item_date_str,                                                               # O: Sale_Date
                "Vinted",                                                                    # P: Platform
                f'=IF(P{next_sold_row}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)', # Q: Packaging_Cost
                f'=IF(AND(ISNUMBER(L{next_sold_row}), ISNUMBER(F{next_sold_row}), ISNUMBER(Q{next_sold_row})), L{next_sold_row}-F{next_sold_row}-Q{next_sold_row}, IF(ISNUMBER(L{next_sold_row}), L{next_sold_row}, 0))', # R: Net_Profit
                f'=IF(AND(ISNUMBER(L{next_sold_row}), L{next_sold_row}>0), R{next_sold_row}/L{next_sold_row}, 0)', # S: Margin_%
                item.favorites or 0,                                                         # T: Favorites
                item.views or 0,                                                             # U: Views
                "",                                                                          # V: Order_ID
                final_url,                                                                   # W: Vinted_URL
                "Sold",                                                                      # X: Status
                item_date_str,                                                               # Y: Date_Listed
                getattr(item, "source", "HTML") or "HTML",                                   # Z: Source
            ]
            batch_new_sold_rows.append(sold_row_data)
            if vid:
                seen_new_vids.add(vid)
            if clean_low:
                seen_new_titles.add(clean_low)

        if batch_cell_updates:
            try:
                ws_sold.batch_update(batch_cell_updates, value_input_option="USER_ENTERED")
            except Exception as e:
                logger.warning(f"Error executing batch_update for sold closet enrichments: {e}")

        new_sold_added = 0
        if batch_new_sold_rows:
            ws_sold.append_rows(batch_new_sold_rows, value_input_option="USER_ENTERED")
            new_sold_added = len(batch_new_sold_rows)

        if moved_count > 0 or new_sold_added > 0:
            try:
                self.sync_lots()
            except Exception:
                pass

        return {"moved_to_sold": moved_count, "new_sold_added": new_sold_added, "enriched": enriched_count}

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
                brand_idx = headers.index("Brand") if "Brand" in headers else 3
                cat_idx = headers.index("Category") if "Category" in headers else 4
                lot_idx = headers.index("Lot_ID") if "Lot_ID" in headers else 1
                url_idx = headers.index("Vinted_URL") if "Vinted_URL" in headers else (22 if tab_name == TAB_SOLD_ITEMS else 14)

                blank_row_indices = []
                for r_idx, row in enumerate(all_rows[1:], start=2):
                    # Check if entirely blank
                    is_empty = not any(str(c).strip() for c in row if c is not None and str(c).strip() not in ("", "None", "nan", "null"))
                    sku_val = str(row[sku_idx]).strip() if (len(row) > sku_idx and row[sku_idx] is not None and str(row[sku_idx]).strip() not in ("None", "nan", "null")) else ""
                    title_val = str(row[title_idx]).strip() if (len(row) > title_idx and row[title_idx] is not None and str(row[title_idx]).strip() not in ("None", "nan", "null")) else ""
                    brand_val = str(row[brand_idx]).strip() if (len(row) > brand_idx and row[brand_idx] is not None and str(row[brand_idx]).strip() not in ("None", "nan", "null")) else ""
                    cat_val = str(row[cat_idx]).strip() if (len(row) > cat_idx and row[cat_idx] is not None and str(row[cat_idx]).strip() not in ("None", "nan", "null")) else ""
                    lot_val = str(row[lot_idx]).strip() if (len(row) > lot_idx and row[lot_idx] is not None and str(row[lot_idx]).strip() not in ("None", "nan", "null")) else ""
                    url_val = str(row[url_idx]).strip() if (len(row) > url_idx and row[url_idx] is not None and str(row[url_idx]).strip() not in ("None", "nan", "null")) else ""

                    # An empty row or a phantom row with NO item identity whatsoever
                    is_phantom = not title_val and not brand_val and not cat_val and not lot_val and not url_val
                    if is_empty or (not sku_val and not title_val) or is_phantom:
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

        self._ensure_headers(ws_listings, LISTINGS_HEADERS)
        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)

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
                else:
                    # Check for duplicate in Sold_Items before appending
                    target_sold_r_i = in_sold_row or self.find_matching_sold_row(
                        sold_rows=sold_rows,
                        headers=s_headers,
                        vinted_id=v_id,
                        vinted_url=item.vinted_url,
                        item_sku=item.item_sku,
                        title=item.title,
                        sold_price=item.sold_price if item.sold_price is not None else curr_val,
                        sale_date=item_date_str,
                        brand=item.brand,
                    )
                    if target_sold_r_i:
                        enrich_updates = self.enrich_sold_row(
                            existing_row=sold_rows[target_sold_r_i - 1],
                            headers=s_headers,
                            row_idx=target_sold_r_i,
                            incoming={
                                "item_title": item.title,
                                "brand": item.brand,
                                "category": item.category,
                                "lot_id": item.lot_id or (date_to_lot_id.get(item_date_str, "") if auto_assign_lot else ""),
                                "vinted_url": item.vinted_url,
                                "sold_price": item.sold_price if item.sold_price is not None else curr_val,
                                "current_price": curr_val,
                                "posted_price": posted_val,
                                "favorites": item.favorites,
                                "views": item.views,
                                "date_listed": item_date_str,
                                "source": getattr(item, "source", "HTML") or "HTML",
                            }
                        )
                        if enrich_updates:
                            sold_updates.extend(enrich_updates)
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
                            getattr(item, "source", "HTML") or "HTML",
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
                        getattr(item, "source", "HTML") or "HTML",
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
        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)
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
        cancelled_count = 0
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

        matched_sold_rows = set()

        for order in scraped_orders:
            clean_title = (order.item_title or "").strip()
            if not clean_title or (order.sold_price is not None and order.sold_price <= 0):
                continue

            if getattr(order, "vinted_status", "Completed") == "Cancelled":
                self.append_cancelled_orders([order])
                cancelled_count += 1
                continue

            sale_date_str = order.sale_date.strftime("%Y-%m-%d") if order.sale_date else datetime.now().strftime("%Y-%m-%d")
            clean_low = clean_title.lower()

            # 1. Check if present in active listings FIRST and move it to Sold_Items
            # ONLY for NEW orders that are not already recorded in Sold_Items!
            is_already_in_sold = bool(order.order_id and order.order_id in existing_order_ids)

            has_active_match = False
            if not is_already_in_sold:
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
                    if order.item_sku:
                        active_sku_to_row.pop(order.item_sku.strip().upper(), None)
                    active_title_to_row.pop(clean_low, None)
                    if order.order_id:
                        existing_order_ids.add(order.order_id)
                    continue

            # 2. Check for duplicate in Sold_Items before appending (enrich existing row if found)
            matched_r_i = self.find_matching_sold_row(
                sold_rows=sold_rows,
                headers=s_headers,
                order_id=order.order_id,
                item_sku=order.item_sku,
                title=clean_title,
                sold_price=order.sold_price,
                sale_date=sale_date_str,
                already_matched_indices=matched_sold_rows,
                brand=order.brand,
            )

            if matched_r_i:
                assigned_lot = default_lot or (date_to_lot_id.get(sale_date_str, "") if auto_assign_lot else "")
                enrich_updates = self.enrich_sold_row(
                    existing_row=sold_rows[matched_r_i - 1],
                    headers=s_headers,
                    row_idx=matched_r_i,
                    incoming={
                        "item_title": clean_title,
                        "brand": order.brand,
                        "category": order.category,
                        "lot_id": assigned_lot,
                        "order_id": order.order_id,
                        "sold_price": order.sold_price,
                        "favorites": order.favorites,
                        "sale_date": sale_date_str,
                        "source": getattr(order, "source", "HTML") or "HTML",
                    },
                )
                if enrich_updates:
                    batch_cell_updates.extend(enrich_updates)
                matched_sold_rows.add(matched_r_i)
                synced_count += 1
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
                getattr(order, "source", "HTML") or "HTML",                   # Z: Source
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

        return {"synced_orders": synced_count, "cancelled_orders": cancelled_count}

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

        self._ensure_headers(ws_listings, LISTINGS_HEADERS)
        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)

        # 1. Single batch get for both listings and sold items (1 read request!)
        batch_ranges = [f"{TAB_ITEM_LISTINGS}!A:S", f"{TAB_SOLD_ITEMS}!A:Z"]
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
            if len(sr) > s_ord_idx and sr[s_ord_idx] is not None and str(sr[s_ord_idx]).strip():
                existing_orders.add(str(sr[s_ord_idx]).strip())

        max_sku_num = self.get_max_sku_num(cached_rows=listings_rows + sold_rows)
        cur_sold_count = len(sold_rows)

        # In-memory mapping of active listings: row_idx -> row list
        active_listings_by_row: Dict[int, List[Any]] = {}
        row_by_vinted_id: Dict[str, int] = {}
        row_by_title: Dict[str, int] = {}
        row_by_url: Dict[str, int] = {}

        for r_i, r in enumerate(listings_rows[1:], start=2):
            active_listings_by_row[r_i] = list(r)
            r_id = str(r[id_idx]).replace(",", "").strip() if (len(r) > id_idx and r[id_idx] is not None) else ""
            r_title = str(r[title_idx]).strip().lower() if (len(r) > title_idx and r[title_idx] is not None) else ""
            r_url = str(r[url_idx]).strip().lower() if (len(r) > url_idx and r[url_idx] is not None) else ""
            if r_id:
                row_by_vinted_id[r_id] = r_i
            if r_title:
                row_by_title[r_title] = r_i
            if r_url:
                row_by_url[r_url] = r_i

        listings_cell_updates: List[Dict[str, Any]] = []
        sold_cell_updates: List[Dict[str, Any]] = []
        rows_to_append_to_sold: List[List[Any]] = []
        rows_to_delete_from_listings: List[int] = []
        matched_sold_indices: set = set()
        results: List[Dict[str, Any]] = []

        col_fav_letter = chr(ord('A') + fav_idx)
        col_hist_letter = chr(ord('A') + hist_idx)

        for event in events:
            clean_target_title = (event.item_title or "").strip().lower()
            clean_target_id = (event.vinted_id or "").replace(",", "").strip() if event.vinted_id else ""
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

            # 3. SALE & BUNDLE SALE
            elif event.event_type in ("sale", "bundle_sale"):
                sale_date_str = event.timestamp.strftime("%Y-%m-%d")
                clean_target_vid = clean_target_id or extract_vinted_id_from_text(event.vinted_url) or extract_vinted_id_from_text(event.item_title) or ""
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

                    item_sku = str(t_data[sku_idx]).strip() if (len(t_data) > sku_idx and t_data[sku_idx] is not None and str(t_data[sku_idx]).strip()) else f"SKU-{max_sku_num + 1}"
                    lot_id = getattr(event, "lot_id", "") or (str(t_data[lot_idx]).strip() if (len(t_data) > lot_idx and t_data[lot_idx] is not None and str(t_data[lot_idx]).strip()) else default_lot)
                    item_title = event.item_title or (str(t_data[title_idx]).strip() if (len(t_data) > title_idx and t_data[title_idx] is not None) else "")
                    brand = getattr(event, "brand", "") or (str(t_data[brand_idx]).strip() if (len(t_data) > brand_idx and t_data[brand_idx] is not None and str(t_data[brand_idx]).strip()) else infer_brand(item_title))
                    category = getattr(event, "category", "") or (str(t_data[cat_idx]).strip() if (len(t_data) > cat_idx and t_data[cat_idx] is not None and str(t_data[cat_idx]).strip()) else infer_clothing_category(item_title))
                    posted_p = t_data[posted_idx] if len(t_data) > posted_idx else event.sold_price
                    curr_p = t_data[curr_idx] if len(t_data) > curr_idx else event.sold_price
                    hist_p = t_data[hist_idx] if len(t_data) > hist_idx else ""
                    fav_val = t_data[fav_idx] if len(t_data) > fav_idx else 0
                    view_val = t_data[views_idx] if len(t_data) > views_idx else 0
                    v_url = event.vinted_url or (f"https://www.vinted.com/items/{clean_target_id}" if clean_target_id else "")

                    # Check if Sold_Items already had a placeholder row for this item before appending
                    matched_sold_r_i = self.find_matching_sold_row(
                        sold_rows=sold_rows,
                        headers=s_headers,
                        order_id=event.order_id,
                        vinted_id=clean_target_vid,
                        vinted_url=v_url,
                        item_sku=item_sku,
                        title=item_title,
                        sold_price=event.sold_price,
                        sale_date=sale_date_str,
                        already_matched_indices=matched_sold_indices,
                        brand=brand,
                    )
                    if matched_sold_r_i:
                        enrich_updates = self.enrich_sold_row(
                            existing_row=sold_rows[matched_sold_r_i - 1],
                            headers=s_headers,
                            row_idx=matched_sold_r_i,
                            incoming={
                                "item_title": item_title,
                                "brand": brand,
                                "category": category,
                                "lot_id": lot_id,
                                "order_id": event.order_id,
                                "vinted_url": v_url,
                                "sold_price": event.sold_price,
                                "favorites": fav_val,
                                "views": view_val,
                                "sale_date": sale_date_str,
                                "source": "Email",
                            },
                        )
                        if enrich_updates:
                            sold_cell_updates.extend(enrich_updates)
                        matched_sold_indices.add(matched_sold_r_i)
                        results.append({"status": "moved_to_sold", "title": item_title, "sku": item_sku, "lot": lot_id})
                        continue

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
                        "Email + HTML",
                    ]
                    rows_to_append_to_sold.append(sold_row)
                    if event.order_id:
                        existing_orders.add(event.order_id)
                    results.append({"status": "moved_to_sold", "title": item_title, "sku": item_sku, "lot": lot_id})

                else:
                    # Item is NOT in active listings. Check if already in Sold_Items before appending!
                    matched_sold_r_i = self.find_matching_sold_row(
                        sold_rows=sold_rows,
                        headers=s_headers,
                        order_id=event.order_id,
                        vinted_id=clean_target_vid,
                        vinted_url=event.vinted_url,
                        title=event.item_title,
                        sold_price=event.sold_price,
                        sale_date=sale_date_str,
                        already_matched_indices=matched_sold_indices,
                        brand=getattr(event, "brand", "") or "",
                    )
                    if matched_sold_r_i:
                        assigned_lot = getattr(event, "lot_id", "") or default_lot
                        enrich_updates = self.enrich_sold_row(
                            existing_row=sold_rows[matched_sold_r_i - 1],
                            headers=s_headers,
                            row_idx=matched_sold_r_i,
                            incoming={
                                "item_title": event.item_title,
                                "brand": getattr(event, "brand", ""),
                                "category": getattr(event, "category", ""),
                                "lot_id": assigned_lot,
                                "order_id": event.order_id,
                                "vinted_url": event.vinted_url,
                                "sold_price": event.sold_price,
                                "sale_date": sale_date_str,
                                "source": "Email",
                            },
                        )
                        if enrich_updates:
                            sold_cell_updates.extend(enrich_updates)
                        matched_sold_indices.add(matched_sold_r_i)
                        results.append({
                            "status": "enriched_sold",
                            "row": matched_sold_r_i,
                            "order_id": event.order_id,
                            "title": event.item_title,
                        })
                        continue

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
                        getattr(event, "source", "Email") or "Email",
                    ]
                    rows_to_append_to_sold.append(sold_row)
                    if event.order_id:
                        existing_orders.add(event.order_id)
                    results.append({"status": "appended_to_sold", "title": assigned_title, "sku": new_sku, "lot": assigned_lot})

            # 4. CANCELLATION
            elif event.event_type == "cancellation":
                canc_order = SalesOrder(
                    order_id=event.order_id or f"ORD-{int(event.timestamp.timestamp())}",
                    item_title=event.item_title or f"Cancelled Order {event.order_id}",
                    sold_price=event.sold_price,
                    sale_date=event.timestamp.date(),
                    platform="Vinted",
                    brand=event.brand or infer_brand(event.item_title or ""),
                    category=event.category or infer_clothing_category(event.item_title or ""),
                    vinted_status="Cancelled",
                    source="Email",
                    buyer_username=event.buyer_username or "",
                    cancellation_reason=getattr(event, "cancellation_reason", "") or "Order cancelled. Refund processed.",
                )
                self.append_cancelled_orders([canc_order])
                results.append({"status": "cancellation_recorded", "order_id": canc_order.order_id, "title": canc_order.item_title})

            else:
                results.append({"status": "ignored"})

        # 4. Flush updates to Google Sheets in single batch operations!
        if listings_cell_updates:
            ws_listings.batch_update(listings_cell_updates, value_input_option="USER_ENTERED")

        if sold_cell_updates:
            ws_sold.batch_update(sold_cell_updates, value_input_option="USER_ENTERED")

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

    def reconcile_missing_sold_metadata(
        self,
        default_lot: str = "",
        gmail_user: str = "",
        gmail_password: str = "",
        max_emails_scan: int = 250,
    ) -> Dict[str, Any]:
        """
        Scans Sold_Items for rows missing Title, Brand, Category, or Lot_ID.
        Cross-references with transactional emails (including those labeled 'Vinted-Processed'),
        active listings in Item_Listings, URL slugs, and category inference to backfill missing fields.
        Returns a dict of counts of updated fields.
        """
        ws_sold = self.get_worksheet(TAB_SOLD_ITEMS)
        ws_listings = self.get_worksheet(TAB_ITEM_LISTINGS)

        self._ensure_headers(ws_sold, SOLD_ITEMS_HEADERS)

        sold_rows = ws_sold.get_all_values()
        listings_rows = ws_listings.get_all_values()

        if len(sold_rows) <= 1:
            return {
                "fixed_rows": 0,
                "titles_fixed": 0,
                "lots_assigned": 0,
                "brands_fixed": 0,
                "categories_fixed": 0,
                "emails_scanned": 0,
                "emails_matched": 0,
            }

        s_headers = sold_rows[0]
        sku_idx = s_headers.index("Item_SKU") if "Item_SKU" in s_headers else 0
        lot_idx = s_headers.index("Lot_ID") if "Lot_ID" in s_headers else 1
        title_idx = s_headers.index("Title") if "Title" in s_headers else 2
        brand_idx = s_headers.index("Brand") if "Brand" in s_headers else 3
        cat_idx = s_headers.index("Category") if "Category" in s_headers else 4
        price_idx = s_headers.index("Sold_Price") if "Sold_Price" in s_headers else 11
        date_idx = s_headers.index("Sale_Date") if "Sale_Date" in s_headers else 14
        order_idx = s_headers.index("Order_ID") if "Order_ID" in s_headers else 21
        url_idx = s_headers.index("Vinted_URL") if "Vinted_URL" in s_headers else 22
        source_idx = s_headers.index("Source") if "Source" in s_headers else -1

        # 1. Optionally scan Gmail for Vinted notification emails (including Vinted-Processed tag)
        emails = []
        if gmail_user and gmail_password:
            try:
                from src.email_parser import fetch_vinted_emails_via_imap
                emails = fetch_vinted_emails_via_imap(
                    username=gmail_user.strip(),
                    password=gmail_password.strip(),
                    folder="ALL",
                    max_emails=max_emails_scan,
                    scan_processed_label=True,
                )
            except Exception as e:
                print(f"[Warning] Failed to fetch emails for sold metadata backfill: {e}")

        # Index fetched emails for fast lookup
        emails_by_order: Dict[str, Any] = {}
        emails_by_vinted_id: Dict[str, Any] = {}
        emails_by_url: Dict[str, Any] = {}
        emails_by_price_date: Dict[Any, List[Any]] = {}

        for ev in emails:
            if getattr(ev, "order_id", ""):
                emails_by_order[ev.order_id.strip().lower()] = ev
            if getattr(ev, "vinted_id", ""):
                emails_by_vinted_id[ev.vinted_id.strip()] = ev
            if getattr(ev, "vinted_url", ""):
                emails_by_url[ev.vinted_url.strip().lower()] = ev
            sp = round(getattr(ev, "sold_price", 0.0), 2)
            sd = normalize_date_str(ev.timestamp.strftime("%Y-%m-%d")) if getattr(ev, "timestamp", None) else ""
            if sp > 0 and sd:
                emails_by_price_date.setdefault((sp, sd), []).append(ev)

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
        phantom_rows_to_delete: List[int] = []
        fixed_rows = 0
        titles_fixed = 0
        lots_assigned = 0
        brands_fixed = 0
        categories_fixed = 0
        emails_matched = 0

        for r_i, row in enumerate(sold_rows[1:], start=2):
            cur_lot = str(row[lot_idx]).strip() if len(row) > lot_idx else ""
            cur_title = str(row[title_idx]).strip() if len(row) > title_idx else ""
            cur_brand = str(row[brand_idx]).strip() if len(row) > brand_idx else ""
            cur_cat = str(row[cat_idx]).strip() if len(row) > cat_idx else ""
            cur_price_raw = str(row[price_idx]).strip() if len(row) > price_idx else ""
            cur_price = parse_price(cur_price_raw)
            cur_date = normalize_date_str(str(row[date_idx]).strip()) if len(row) > date_idx else ""
            cur_order = str(row[order_idx]).strip() if len(row) > order_idx else ""
            cur_url = str(row[url_idx]).strip() if len(row) > url_idx else ""
            cur_source = str(row[source_idx]).strip() if (source_idx != -1 and len(row) > source_idx) else ""

            # Check if this row is an empty phantom duplicate row lacking all item identity
            if not cur_title and not cur_brand and not cur_cat and not cur_lot and not cur_url:
                phantom_rows_to_delete.append(r_i)
                continue

            # Check if this row needs fixing
            needs_title = not cur_title or cur_title.lower().startswith("order ") or cur_title.lower().startswith("vinted item")
            needs_lot = not cur_lot and bool(default_lot)
            needs_brand = not cur_brand or cur_brand.lower() in ("unbranded", "none", "unknown")
            needs_cat = not cur_cat or cur_cat.lower() in ("other apparel", "none", "unknown")
            needs_order = not cur_order
            needs_url = not cur_url
            needs_source = (source_idx != -1 and not cur_source)

            if not (needs_title or needs_lot or needs_brand or needs_cat or needs_order or needs_url or needs_source):
                continue

            matched_ev: Any = None

            # 1. Match email by Order_ID
            if cur_order:
                matched_ev = emails_by_order.get(cur_order.lower())

            # 2. Match email by UUID in Title (e.g. "Order 01a1569f-be78...")
            if not matched_ev and cur_title:
                uuid_m = re.search(r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", cur_title, re.IGNORECASE)
                if uuid_m:
                    matched_ev = emails_by_order.get(uuid_m.group(1).lower())

            # 3. Match email by UUID in URL (e.g. "...transaction/01a1569f-be78...")
            if not matched_ev and cur_url:
                uuid_m = re.search(r"/(?:transaction|inbox)/([a-f0-9-]+)", cur_url, re.IGNORECASE)
                if uuid_m:
                    matched_ev = emails_by_order.get(uuid_m.group(1).lower())

            # 4. Match email by Vinted ID in URL
            if not matched_ev and cur_url:
                vid_m = re.search(r"/items/(\d+)", cur_url)
                if vid_m:
                    matched_ev = emails_by_vinted_id.get(vid_m.group(1))

            # 5. Match email by exact URL
            if not matched_ev and cur_url:
                matched_ev = emails_by_url.get(cur_url.lower())

            # 6. Match email by price and sale date (if unique match)
            if not matched_ev and cur_price > 0 and cur_date:
                cands = emails_by_price_date.get((round(cur_price, 2), cur_date))
                if cands and len(cands) == 1:
                    matched_ev = cands[0]

            if matched_ev:
                emails_matched += 1

            # Match active listings as secondary source
            v_id_match = re.search(r"/items/(\d+)", cur_url) if cur_url else None
            v_id = v_id_match.group(1) if v_id_match else ""
            matched_listing = (
                listings_by_id.get(v_id)
                or listings_by_url.get(cur_url.lower())
                or (listings_by_title.get(cur_title.lower()) if cur_title else None)
            )

            row_changed = False

            # 1. Backfill Title
            if needs_title:
                new_title = ""
                if matched_ev and getattr(matched_ev, "item_title", ""):
                    new_title = matched_ev.item_title.strip()
                elif matched_listing and len(matched_listing) > l_title_idx and matched_listing[l_title_idx].strip():
                    new_title = matched_listing[l_title_idx].strip()
                elif cur_url:
                    slug_match = re.search(r"/items/\d+-([a-zA-Z0-9_-]+)", cur_url)
                    if slug_match:
                        new_title = slug_match.group(1).replace("-", " ").replace("_", " ").title().strip()

                # Guaranteed fallback if still empty so no sold item is left titleless
                if not new_title and not cur_title:
                    if cur_brand and cur_cat:
                        new_title = f"{cur_brand} {cur_cat}"
                    elif cur_brand:
                        new_title = f"{cur_brand} Apparel"
                    elif cur_order and not cur_order.lower().startswith("ord-17"):
                        new_title = f"Vinted Order {cur_order}"
                    elif cur_date:
                        new_title = f"Vinted Sale ({cur_date})"
                    else:
                        new_title = "Vinted Item"

                if new_title and new_title != cur_title:
                    cur_title = new_title
                    col_t = chr(ord('A') + title_idx)
                    updates.append({"range": f"{col_t}{r_i}", "values": [[new_title]]})
                    titles_fixed += 1
                    row_changed = True

            # 2. Backfill Lot_ID
            if not cur_lot:
                new_lot = ""
                if matched_ev and getattr(matched_ev, "lot_id", ""):
                    new_lot = matched_ev.lot_id.strip()
                elif matched_listing and len(matched_listing) > l_lot_idx and matched_listing[l_lot_idx].strip():
                    new_lot = matched_listing[l_lot_idx].strip()
                elif default_lot:
                    new_lot = default_lot

                if new_lot:
                    cur_lot = new_lot
                    col_l = chr(ord('A') + lot_idx)
                    updates.append({"range": f"{col_l}{r_i}", "values": [[new_lot]]})
                    lots_assigned += 1
                    row_changed = True

            # 3. Backfill Brand
            if needs_brand:
                new_brand = ""
                if matched_ev and getattr(matched_ev, "brand", "") and matched_ev.brand.lower() not in ("unbranded", "none", "unknown"):
                    new_brand = matched_ev.brand.strip()
                elif matched_listing and len(matched_listing) > l_brand_idx and matched_listing[l_brand_idx].strip():
                    new_brand = matched_listing[l_brand_idx].strip()
                elif cur_title:
                    new_brand = infer_brand(cur_title)

                if new_brand and new_brand != cur_brand:
                    cur_brand = new_brand
                    col_b = chr(ord('A') + brand_idx)
                    updates.append({"range": f"{col_b}{r_i}", "values": [[new_brand]]})
                    brands_fixed += 1
                    row_changed = True

            # 4. Backfill Category
            if needs_cat:
                new_cat = ""
                if matched_ev and getattr(matched_ev, "category", "") and matched_ev.category.lower() not in ("other apparel", "none", "unknown"):
                    new_cat = matched_ev.category.strip()
                elif matched_listing and len(matched_listing) > l_cat_idx and matched_listing[l_cat_idx].strip():
                    new_cat = matched_listing[l_cat_idx].strip()
                elif cur_title:
                    new_cat = infer_clothing_category(cur_title)

                if new_cat and new_cat != cur_cat:
                    cur_cat = new_cat
                    col_c = chr(ord('A') + cat_idx)
                    updates.append({"range": f"{col_c}{r_i}", "values": [[new_cat]]})
                    categories_fixed += 1
                    row_changed = True

            # 5. Backfill Order_ID if missing
            if not cur_order and matched_ev and getattr(matched_ev, "order_id", ""):
                cur_order = matched_ev.order_id.strip()
                col_o = chr(ord('A') + order_idx)
                updates.append({"range": f"{col_o}{r_i}", "values": [[cur_order]]})
                row_changed = True

            # 6. Backfill Vinted_URL if missing
            if not cur_url and matched_ev and getattr(matched_ev, "vinted_url", ""):
                cur_url = matched_ev.vinted_url.strip()
                col_u = chr(ord('A') + url_idx)
                updates.append({"range": f"{col_u}{r_i}", "values": [[cur_url]]})
                row_changed = True

            # 7. Backfill Source if missing
            if needs_source:
                if matched_ev and matched_listing:
                    new_source = "Email + HTML"
                elif matched_ev or cur_order.lower().startswith("ord-"):
                    new_source = "Email"
                else:
                    new_source = "HTML"
                col_s = col_letter(source_idx)
                updates.append({"range": f"{col_s}{r_i}", "values": [[new_source]]})
                row_changed = True

            if row_changed:
                fixed_rows += 1

        if updates:
            ws_sold.batch_update(updates, value_input_option="USER_ENTERED")
            self.sync_lots()

        if phantom_rows_to_delete:
            for r_del in sorted(phantom_rows_to_delete, reverse=True):
                try:
                    ws_sold.delete_rows(r_del)
                    fixed_rows += 1
                except Exception:
                    pass
            self.sync_lots()

        return {
            "fixed_rows": fixed_rows,
            "titles_fixed": titles_fixed,
            "lots_assigned": lots_assigned,
            "brands_fixed": brands_fixed,
            "categories_fixed": categories_fixed,
            "emails_scanned": len(emails),
            "emails_matched": emails_matched,
        }

    def find_duplicate_groups(self, tab_name: str = TAB_SOLD_ITEMS) -> List[Dict[str, Any]]:
        """
        Scans worksheet rows to detect duplicate item groups based on:
        - Matching Order_ID
        - Matching Transaction/Order UUID
        - Matching Vinted Item ID
        - Matching Vinted URL
        - Matching descriptive Title + Price
        - Matching Sold_Price + Sale_Date with at least one placeholder title
        Returns a list of duplicate groups with comparison metrics and recommended keep row.
        """
        ws = self.get_worksheet(tab_name)
        rows = ws.get_all_values()
        if len(rows) <= 1:
            return []

        headers = rows[0]
        sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
        lot_idx = headers.index("Lot_ID") if "Lot_ID" in headers else 1
        title_idx = headers.index("Title") if "Title" in headers else 2
        brand_idx = headers.index("Brand") if "Brand" in headers else 3
        cat_idx = headers.index("Category") if "Category" in headers else 4
        price_idx = headers.index("Sold_Price") if "Sold_Price" in headers else (headers.index("Current_Price") if "Current_Price" in headers else -1)
        date_idx = headers.index("Sale_Date") if "Sale_Date" in headers else (headers.index("Date_Listed") if "Date_Listed" in headers else -1)
        order_idx = headers.index("Order_ID") if "Order_ID" in headers else -1
        vid_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else -1
        url_idx = headers.index("Vinted_URL") if "Vinted_URL" in headers else -1
        source_idx = headers.index("Source") if "Source" in headers else -1

        items: List[Dict[str, Any]] = []
        for r_i, r in enumerate(rows[1:], start=2):
            sku_val = str(r[sku_idx]).strip() if (len(r) > sku_idx and r[sku_idx] is not None) else ""
            title_val = str(r[title_idx]).strip() if (len(r) > title_idx and r[title_idx] is not None) else ""
            lot_val = str(r[lot_idx]).strip() if (len(r) > lot_idx and r[lot_idx] is not None) else ""
            brand_val = str(r[brand_idx]).strip() if (len(r) > brand_idx and r[brand_idx] is not None) else ""
            cat_val = str(r[cat_idx]).strip() if (len(r) > cat_idx and r[cat_idx] is not None) else ""
            price_val = parse_price(r[price_idx]) if (price_idx != -1 and len(r) > price_idx) else 0.0
            date_val = normalize_date_str(r[date_idx]) if (date_idx != -1 and len(r) > date_idx) else ""
            ord_val = str(r[order_idx]).strip() if (order_idx != -1 and len(r) > order_idx and r[order_idx] is not None) else ""
            vid_val = str(r[vid_idx]).strip() if (vid_idx != -1 and len(r) > vid_idx and r[vid_idx] is not None) else ""
            url_val = str(r[url_idx]).strip() if (url_idx != -1 and len(r) > url_idx and r[url_idx] is not None) else ""
            source_val = str(r[source_idx]).strip() if (source_idx != -1 and len(r) > source_idx and r[source_idx] is not None) else ""
            if not source_val:
                if ord_val.lower().startswith("ord-"):
                    source_val = "Email"
                elif ord_val or url_val:
                    source_val = "HTML"
                else:
                    source_val = "—"

            # Skip empty rows
            if not sku_val and not title_val and not ord_val and not url_val and price_val == 0.0:
                continue

            # Completeness score (higher = more populated & higher fidelity)
            score = 0
            if not is_placeholder_title(title_val):
                score += 4
            elif title_val:
                score += 1
            if not is_placeholder_brand(brand_val):
                score += 2
            if not is_placeholder_category(cat_val):
                score += 2
            if not is_placeholder_lot(lot_val):
                score += 2
            if not is_placeholder_order_id(ord_val):
                score += 3
            if url_val and url_val.startswith("http"):
                score += 2
            if price_val > 0:
                score += 1
            if date_val:
                score += 1

            items.append({
                "row_idx": r_i,
                "sku": sku_val,
                "lot": lot_val,
                "lot_id": lot_val,
                "title": title_val,
                "brand": brand_val,
                "category": cat_val,
                "price": price_val,
                "date": date_val,
                "order_id": ord_val,
                "vinted_id": vid_val,
                "url": url_val,
                "source": source_val,
                "completeness_score": score,
                "raw_row": list(r),
            })

        graph: Dict[int, set] = {}
        edge_reasons: Dict[tuple, str] = {}
        item_by_row: Dict[int, Dict[str, Any]] = {it["row_idx"]: it for it in items}

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a = items[i]
                b = items[j]
                r_a = a["row_idx"]
                r_b = b["row_idx"]

                is_dup = False
                reason = ""

                # Check 1: Matching non-empty Order_ID
                if a["order_id"] and b["order_id"] and a["order_id"].lower() not in ("none", "nan", "null"):
                    if a["order_id"].lower() == b["order_id"].lower():
                        is_dup = True
                        reason = f"Matching Order ID: {a['order_id']}"

                # Check 2: Matching UUID
                if not is_dup:
                    uuid_a = extract_uuid(a["order_id"]) or extract_uuid(a["url"]) or extract_uuid(a["title"])
                    uuid_b = extract_uuid(b["order_id"]) or extract_uuid(b["url"]) or extract_uuid(b["title"])
                    if uuid_a and uuid_b and uuid_a == uuid_b:
                        is_dup = True
                        reason = f"Matching Transaction UUID: {uuid_a}"

                # Check 3: Matching numeric Vinted ID
                if not is_dup:
                    vid_a = a["vinted_id"] or extract_vinted_id_from_text(a["url"]) or extract_vinted_id_from_text(a["title"])
                    vid_b = b["vinted_id"] or extract_vinted_id_from_text(b["url"]) or extract_vinted_id_from_text(b["title"])
                    if vid_a and vid_b and vid_a == vid_b:
                        is_dup = True
                        reason = f"Matching Vinted Item ID: {vid_a}"

                # Check 4: Exact Vinted URL
                if not is_dup:
                    u_a = a["url"].strip().lower()
                    u_b = b["url"].strip().lower()
                    if u_a and u_b and (u_a == u_b or u_a.split("?")[0] == u_b.split("?")[0]):
                        is_dup = True
                        reason = "Identical Vinted URL"

                # Check 5: Real Title match with same price (only if brands do not conflict)
                if not is_dup:
                    t_a = a["title"].strip().lower()
                    t_b = b["title"].strip().lower()
                    b_a = a["brand"].strip().lower()
                    b_b = b["brand"].strip().lower()
                    eff_b_a = b_a if not is_placeholder_brand(b_a) else (infer_brand(a["title"]) or "").strip().lower()
                    eff_b_b = b_b if not is_placeholder_brand(b_b) else (infer_brand(b["title"]) or "").strip().lower()
                    brands_differ = (
                        not is_placeholder_brand(eff_b_a)
                        and not is_placeholder_brand(eff_b_b)
                        and eff_b_a != eff_b_b
                    )
                    if not brands_differ and t_a and t_b and not is_placeholder_title(t_a) and not is_placeholder_title(t_b):
                        if t_a == t_b:
                            if a["price"] == 0 or b["price"] == 0 or abs(a["price"] - b["price"]) < 0.01:
                                is_dup = True
                                reason = f"Identical Title: '{a['title']}'"
                        elif len(t_a) >= 12 and (t_a in t_b or t_b in t_a):
                            if a["price"] > 0 and abs(a["price"] - b["price"]) < 0.01:
                                is_dup = True
                                reason = f"Matching Title: '{a['title']}'"

                # Check 6: Matching Sold Price & Date with at least one placeholder title
                if not is_dup:
                    b_a = a["brand"].strip().lower()
                    b_b = b["brand"].strip().lower()
                    eff_b_a = b_a if not is_placeholder_brand(b_a) else (infer_brand(a["title"]) or "").strip().lower()
                    eff_b_b = b_b if not is_placeholder_brand(b_b) else (infer_brand(b["title"]) or "").strip().lower()
                    brands_differ = (
                        not is_placeholder_brand(eff_b_a)
                        and not is_placeholder_brand(eff_b_b)
                        and eff_b_a != eff_b_b
                    )
                    if not brands_differ:
                        if is_placeholder_title(a["title"]) or is_placeholder_title(b["title"]):
                            if a["price"] > 0 and b["price"] > 0 and abs(a["price"] - b["price"]) < 0.01:
                                d_a = a["date"]
                                d_b = b["date"]
                                if d_a and d_b:
                                    try:
                                        dt_a = datetime.strptime(d_a, "%Y-%m-%d")
                                        dt_b = datetime.strptime(d_b, "%Y-%m-%d")
                                        if abs((dt_a - dt_b).days) <= 2:
                                            is_dup = True
                                            reason = f"Same Price (${a['price']:.2f}) & Date ({d_a}) with placeholder title"
                                    except Exception:
                                        if d_a == d_b:
                                            is_dup = True
                                            reason = f"Same Price (${a['price']:.2f}) & Date ({d_a}) with placeholder title"
                                elif not d_a or not d_b:
                                    is_dup = True
                                    reason = f"Same Price (${a['price']:.2f}) with placeholder title"

                if is_dup:
                    graph.setdefault(r_a, set()).add(r_b)
                    graph.setdefault(r_b, set()).add(r_a)
                    edge_reasons[(min(r_a, r_b), max(r_a, r_b))] = reason

        # Find connected components
        visited = set()
        groups: List[Dict[str, Any]] = []
        group_counter = 1

        for r_start in sorted(graph.keys()):
            if r_start in visited:
                continue

            comp = []
            queue = [r_start]
            visited.add(r_start)

            while queue:
                curr = queue.pop(0)
                comp.append(curr)
                for neighbor in graph.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            comp.sort()
            reasons = set()
            for x_i in range(len(comp)):
                for y_i in range(x_i + 1, len(comp)):
                    key = (min(comp[x_i], comp[y_i]), max(comp[x_i], comp[y_i]))
                    if key in edge_reasons:
                        reasons.add(edge_reasons[key])

            comp_items = [item_by_row[r] for r in comp]
            best_row = max(comp_items, key=lambda it: (it["completeness_score"], -it["row_idx"]))

            groups.append({
                "group_id": f"group_{group_counter}",
                "reason": " • ".join(sorted(reasons)) if reasons else "Duplicate item detected",
                "rows": comp_items,
                "recommended_keep_row_idx": best_row["row_idx"],
            })
            group_counter += 1

        return groups

    def resolve_duplicates(
        self,
        tab_name: str,
        decisions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Executes user decisions on duplicate groups:
        - "keep_row": retains keep_row_idx, deletes discard_row_indices
        - "merge_into": enriches keep_row_idx with fields from discard rows, then deletes discard rows
        - "keep_both": keeps all rows in the group without deleting anything
        """
        ws = self.get_worksheet(tab_name)
        all_rows = ws.get_all_values()
        headers = all_rows[0] if all_rows else (SOLD_ITEMS_HEADERS if tab_name == TAB_SOLD_ITEMS else LISTINGS_HEADERS)

        deleted_rows = set()
        merged_count = 0
        kept_both_count = 0
        cell_updates = []

        for dec in decisions:
            action = dec.get("action", "keep_row")
            if action == "keep_both":
                kept_both_count += 1
                continue

            keep_idx = dec.get("keep_row_idx")
            discard_indices = dec.get("discard_row_indices", [])

            if not keep_idx or not discard_indices:
                continue

            if action == "merge_into" and keep_idx <= len(all_rows):
                keep_row_data = all_rows[keep_idx - 1]
                for d_idx in discard_indices:
                    if d_idx <= len(all_rows):
                        d_row_data = all_rows[d_idx - 1]
                        inc = {}
                        title_i = headers.index("Title") if "Title" in headers else 2
                        brand_i = headers.index("Brand") if "Brand" in headers else 3
                        cat_i = headers.index("Category") if "Category" in headers else 4
                        lot_i = headers.index("Lot_ID") if "Lot_ID" in headers else 1
                        ord_i = headers.index("Order_ID") if "Order_ID" in headers else -1
                        url_i = headers.index("Vinted_URL") if "Vinted_URL" in headers else -1
                        sp_i = headers.index("Sold_Price") if "Sold_Price" in headers else -1
                        date_i = headers.index("Sale_Date") if "Sale_Date" in headers else -1
                        src_i = headers.index("Source") if "Source" in headers else -1

                        if len(d_row_data) > title_i and d_row_data[title_i] is not None and str(d_row_data[title_i]).strip():
                            inc["item_title"] = str(d_row_data[title_i]).strip()
                        if len(d_row_data) > brand_i and d_row_data[brand_i] is not None and str(d_row_data[brand_i]).strip():
                            inc["brand"] = str(d_row_data[brand_i]).strip()
                        if len(d_row_data) > cat_i and d_row_data[cat_i] is not None and str(d_row_data[cat_i]).strip():
                            inc["category"] = str(d_row_data[cat_i]).strip()
                        if len(d_row_data) > lot_i and d_row_data[lot_i] is not None and str(d_row_data[lot_i]).strip():
                            inc["lot_id"] = str(d_row_data[lot_i]).strip()
                        if ord_i != -1 and len(d_row_data) > ord_i and d_row_data[ord_i] is not None and str(d_row_data[ord_i]).strip():
                            inc["order_id"] = str(d_row_data[ord_i]).strip()
                        if url_i != -1 and len(d_row_data) > url_i and d_row_data[url_i] is not None and str(d_row_data[url_i]).strip():
                            inc["vinted_url"] = str(d_row_data[url_i]).strip()
                        if sp_i != -1 and len(d_row_data) > sp_i and parse_price(d_row_data[sp_i]) > 0:
                            inc["sold_price"] = parse_price(d_row_data[sp_i])
                        if date_i != -1 and len(d_row_data) > date_i and d_row_data[date_i] is not None and str(d_row_data[date_i]).strip():
                            inc["sale_date"] = str(d_row_data[date_i]).strip()
                        if src_i != -1 and len(d_row_data) > src_i and d_row_data[src_i] is not None and str(d_row_data[src_i]).strip():
                            inc["source"] = str(d_row_data[src_i]).strip()

                        if tab_name == TAB_SOLD_ITEMS:
                            enrich_u = self.enrich_sold_row(keep_row_data, headers, keep_idx, inc)
                            if enrich_u:
                                cell_updates.extend(enrich_u)
                                merged_count += 1
                        else:
                            # Dedicated Item_Listings / non-Sold merging logic
                            list_u = []
                            cur_t = str(keep_row_data[title_i] if len(keep_row_data) > title_i else "").strip()
                            new_t = inc.get("item_title", "")
                            if new_t and not is_placeholder_title(new_t) and (not cur_t or is_placeholder_title(cur_t)):
                                list_u.append({"range": f"{col_letter(title_i)}{keep_idx}", "values": [[new_t]]})
                                while len(keep_row_data) <= title_i:
                                    keep_row_data.append("")
                                keep_row_data[title_i] = new_t

                            cur_b = str(keep_row_data[brand_i] if len(keep_row_data) > brand_i else "").strip()
                            new_b = inc.get("brand", "")
                            if new_b and not is_placeholder_brand(new_b) and (not cur_b or is_placeholder_brand(cur_b)):
                                list_u.append({"range": f"{col_letter(brand_i)}{keep_idx}", "values": [[new_b]]})
                                while len(keep_row_data) <= brand_i:
                                    keep_row_data.append("")
                                keep_row_data[brand_i] = new_b

                            cur_c = str(keep_row_data[cat_i] if len(keep_row_data) > cat_i else "").strip()
                            new_c = inc.get("category", "")
                            if new_c and not is_placeholder_category(new_c) and (not cur_c or is_placeholder_category(cur_c)):
                                list_u.append({"range": f"{col_letter(cat_i)}{keep_idx}", "values": [[new_c]]})
                                while len(keep_row_data) <= cat_i:
                                    keep_row_data.append("")
                                keep_row_data[cat_i] = new_c

                            cur_l = str(keep_row_data[lot_i] if len(keep_row_data) > lot_i else "").strip()
                            new_l = inc.get("lot_id", "")
                            if new_l and not is_placeholder_lot(new_l) and (not cur_l or is_placeholder_lot(cur_l)):
                                list_u.append({"range": f"{col_letter(lot_i)}{keep_idx}", "values": [[new_l]]})
                                while len(keep_row_data) <= lot_i:
                                    keep_row_data.append("")
                                keep_row_data[lot_i] = new_l
                                cost_i = headers.index("Unit_Cost") if "Unit_Cost" in headers else 5
                                col_b_let = col_letter(lot_i)
                                cost_formula = f'=IFERROR(VLOOKUP({col_b_let}{keep_idx}, Inventory_Lots!A:F, 6, FALSE), 0)'
                                list_u.append({"range": f"{col_letter(cost_i)}{keep_idx}", "values": [[cost_formula]]})

                            if url_i != -1:
                                cur_u = str(keep_row_data[url_i] if len(keep_row_data) > url_i else "").strip()
                                new_u = inc.get("vinted_url", "")
                                if new_u and (not cur_u or not cur_u.startswith("http")):
                                    list_u.append({"range": f"{col_letter(url_i)}{keep_idx}", "values": [[new_u]]})
                                    while len(keep_row_data) <= url_i:
                                        keep_row_data.append("")
                                    keep_row_data[url_i] = new_u

                            vid_i = headers.index("Vinted_ID") if "Vinted_ID" in headers else -1
                            if vid_i != -1 and len(d_row_data) > vid_i:
                                d_vid = str(d_row_data[vid_i] or "").strip()
                                cur_vid = str(keep_row_data[vid_i] if len(keep_row_data) > vid_i else "").strip()
                                if d_vid and not cur_vid:
                                    list_u.append({"range": f"{col_letter(vid_i)}{keep_idx}", "values": [[d_vid]]})
                                    while len(keep_row_data) <= vid_i:
                                        keep_row_data.append("")
                                    keep_row_data[vid_i] = d_vid

                            if src_i != -1:
                                cur_src = str(keep_row_data[src_i] if len(keep_row_data) > src_i else "").strip()
                                new_src = inc.get("source", "")
                                if new_src and not cur_src:
                                    list_u.append({"range": f"{col_letter(src_i)}{keep_idx}", "values": [[new_src]]})
                                    while len(keep_row_data) <= src_i:
                                        keep_row_data.append("")
                                    keep_row_data[src_i] = new_src

                            if list_u:
                                cell_updates.extend(list_u)
                                merged_count += 1

            for d_idx in discard_indices:
                deleted_rows.add(d_idx)

        # Apply cell updates first if any
        if cell_updates:
            ws.batch_update(cell_updates, value_input_option="USER_ENTERED")

        # Delete discarded rows in descending order
        deleted_count = 0
        for r_del in sorted(deleted_rows, reverse=True):
            try:
                ws.delete_rows(r_del)
                deleted_count += 1
            except Exception as e:
                print(f"Error deleting row {r_del}: {e}")

        if tab_name == TAB_SOLD_ITEMS:
            try:
                self.sync_lots()
            except Exception:
                pass

        return {
            "deleted_count": deleted_count,
            "merged_count": merged_count,
            "kept_both_count": kept_both_count,
        }

    def manual_move_item(
        self,
        source_tab: str,
        dest_tab: str,
        source_sheet_row: int,
        item_data: Optional[Dict[str, Any]] = None,
        delete_source: bool = True,
    ) -> Dict[str, Any]:
        """
        Moves or relists an item from source_tab to dest_tab at 1-based source_sheet_row.
        Generates proper destination schema and formulas, appends to dest_tab.
        If delete_source is True, deletes source_sheet_row from source_tab.
        If delete_source is False (e.g. relisting a cancelled order), preserves the source row
        and marks it as relisted in Status_Details.
        """
        from src.sheets.reconcile import build_manual_move_row, C

        ws_source = self.get_worksheet(source_tab)
        ws_dest = self.get_worksheet(dest_tab)

        source_all = ws_source.get_all_values()
        if source_sheet_row < 2 or source_sheet_row > len(source_all):
            raise ValueError(f"Invalid row {source_sheet_row} for tab {source_tab} (total rows: {len(source_all)})")

        source_row = source_all[source_sheet_row - 1]
        dest_all = ws_dest.get_all_values()
        dest_sheet_row = len(dest_all) + 1

        if not item_data:
            item_data = {}

        # If moving/relisting to Item_Listings and no SKU exists, assign the next sequential SKU
        if dest_tab == TAB_ITEM_LISTINGS and not item_data.get("sku"):
            s_sku = ""
            if source_tab == TAB_CANCELLED_ORDERS and len(source_row) > C["Item_SKU"]:
                s_sku = str(source_row[C["Item_SKU"]]).strip()
            if s_sku:
                item_data["sku"] = s_sku
            else:
                next_sku_num = self.get_max_sku_num() + 1
                item_data["sku"] = f"SKU-{next_sku_num}"

        new_row = build_manual_move_row(
            source_tab=source_tab,
            dest_tab=dest_tab,
            source_row=source_row,
            dest_sheet_row=dest_sheet_row,
            item_data=item_data,
        )

        ws_dest.append_rows([new_row], value_input_option="USER_ENTERED")

        if delete_source:
            ws_source.delete_rows(source_sheet_row)
        else:
            # Annotate source row without deleting (e.g. preserves cancelled orders audit trail)
            if source_tab == TAB_CANCELLED_ORDERS and len(source_row) > C["Status_Details"]:
                cur_det = str(source_row[C["Status_Details"]]).strip()
                if "Relisted" not in cur_det:
                    new_det = f"{cur_det} | Relisted to Active Listings" if cur_det else "Relisted to Active Listings"
                    col_c = col_letter(C["Status_Details"])
                    ws_source.batch_update([{"range": f"{col_c}{source_sheet_row}", "values": [[new_det]]}])

        if source_tab == TAB_SOLD_ITEMS or dest_tab == TAB_SOLD_ITEMS:
            try:
                self.sync_lots()
            except Exception as e:
                logger.warning(f"sync_lots failed during manual move: {e}")

        return {"status": "success", "dest_tab": dest_tab, "dest_row": dest_sheet_row}

    def manual_update_row(
        self,
        tab_name: str,
        sheet_row: int,
        updated_fields: Dict[str, Any],
    ) -> bool:
        """
        Updates specific named columns in-place on tab_name at 1-based sheet_row.
        """
        from src.sheets.reconcile import L, S, C

        if tab_name in (TAB_ITEM_LISTINGS, TAB_HIDDEN_ITEMS):
            h_map = L
        elif tab_name == TAB_SOLD_ITEMS:
            h_map = S
        elif tab_name == TAB_CANCELLED_ORDERS:
            h_map = C
        else:
            raise ValueError(f"Unsupported tab for update: {tab_name}")

        ws = self.get_worksheet(tab_name)
        updates = []
        for field, val in updated_fields.items():
            if field in h_map:
                col_c = col_letter(h_map[field])
                updates.append({"range": f"{col_c}{sheet_row}", "values": [[val]]})

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

        if tab_name == TAB_SOLD_ITEMS or "Lot_ID" in updated_fields:
            try:
                self.sync_lots()
            except Exception as e:
                logger.warning(f"sync_lots failed during manual update: {e}")

        return True

    def manual_delete_row(
        self,
        tab_name: str,
        sheet_row: int,
    ) -> bool:
        """
        Permanently deletes a row at 1-based sheet_row from tab_name in Google Sheets.
        Prevents deleting row 1 (header row).
        """
        ws = self.get_worksheet(tab_name)
        rows = ws.get_all_values()
        if sheet_row < 2 or sheet_row > len(rows):
            raise ValueError(f"Invalid row {sheet_row} for tab {tab_name} (total rows: {len(rows)})")

        ws.delete_rows(sheet_row)

        if tab_name == TAB_SOLD_ITEMS:
            try:
                self.sync_lots()
            except Exception as e:
                logger.warning(f"sync_lots failed during manual row deletion: {e}")

        return True


def reconcile_missing_sold_metadata(
    engine: SheetsSyncEngine,
    default_lot: str = "",
    gmail_user: str = "",
    gmail_password: str = "",
    max_emails_scan: int = 250,
) -> Dict[str, Any]:
    """Module-level helper to backfill missing metadata in Sold_Items."""
    return engine.reconcile_missing_sold_metadata(
        default_lot=default_lot,
        gmail_user=gmail_user,
        gmail_password=gmail_password,
        max_emails_scan=max_emails_scan,
    )




