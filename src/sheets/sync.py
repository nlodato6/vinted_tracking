"""Synchronization logic for reconciling scraped Vinted data with Google Sheets."""

from datetime import datetime
from typing import List, Dict, Any, Optional
import gspread
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import (
    TAB_ITEM_LISTINGS,
    TAB_SALES_LOG,
    TAB_INVENTORY_LOTS,
)
from src.models import ItemListing, SalesOrder


class SheetsSyncEngine:
    def __init__(self, client: GoogleSheetsClient):
        self.client = client

    def get_listings_records(self) -> List[Dict[str, Any]]:
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_ITEM_LISTINGS)
        return ws.get_all_records()

    def get_sales_records(self) -> List[Dict[str, Any]]:
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_SALES_LOG)
        return ws.get_all_records()

    def sync_listings(self, scraped_items: List[ItemListing]) -> Dict[str, int]:
        """
        Updates live prices and statuses of existing listings,
        and appends new listings discovered on Vinted.
        """
        sh = self.client.get_spreadsheet()
        ws = sh.worksheet(TAB_ITEM_LISTINGS)
        all_rows = ws.get_all_values()

        if not all_rows:
            return {"updated": 0, "created": 0}

        headers = all_rows[0]
        id_col_idx = headers.index("Vinted_ID") if "Vinted_ID" in headers else 10
        title_col_idx = headers.index("Title") if "Title" in headers else 2
        price_col_idx = headers.index("Current_Price") if "Current_Price" in headers else 7
        fav_col_idx = headers.index("Favorites") if "Favorites" in headers else 8
        views_col_idx = headers.index("Views") if "Views" in headers else 9
        status_col_idx = headers.index("Status") if "Status" in headers else 12
        synced_col_idx = headers.index("Last_Synced") if "Last_Synced" in headers else 14
        sku_col_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0

        # Map existing Vinted IDs and Titles to row index (1-based)
        existing_by_vinted_id: Dict[str, int] = {}
        existing_by_title: Dict[str, int] = {}
        max_sku_num = 1000

        for row_idx, row in enumerate(all_rows[1:], start=2):
            if len(row) > id_col_idx and row[id_col_idx].strip():
                existing_by_vinted_id[row[id_col_idx].strip()] = row_idx
            if len(row) > title_col_idx and row[title_col_idx].strip():
                existing_by_title[row[title_col_idx].strip().lower()] = row_idx
            if len(row) > sku_col_idx and row[sku_col_idx].startswith("SKU-"):
                try:
                    num = int(row[sku_col_idx].replace("SKU-", ""))
                    if num > max_sku_num:
                        max_sku_num = num
                except ValueError:
                    pass

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates = []
        new_rows = []
        updated_count = 0

        for item in scraped_items:
            v_id = item.vinted_id.strip() if item.vinted_id else ""
            clean_title = item.title.strip().lower()

            target_row = existing_by_vinted_id.get(v_id) or existing_by_title.get(clean_title)

            if target_row:
                # Update existing row: price, favorites, views, status, last_synced
                col_letter_price = chr(ord('A') + price_col_idx)
                col_letter_fav = chr(ord('A') + fav_col_idx)
                col_letter_views = chr(ord('A') + views_col_idx)
                col_letter_status = chr(ord('A') + status_col_idx)
                col_letter_synced = chr(ord('A') + synced_col_idx)

                updates.append({
                    "range": f"{col_letter_price}{target_row}",
                    "values": [[item.current_price]],
                })
                updates.append({
                    "range": f"{col_letter_fav}{target_row}",
                    "values": [[item.favorites]],
                })
                updates.append({
                    "range": f"{col_letter_views}{target_row}",
                    "values": [[item.views]],
                })
                if item.status:
                    updates.append({
                        "range": f"{col_letter_status}{target_row}",
                        "values": [[item.status]],
                    })
                updates.append({
                    "range": f"{col_letter_synced}{target_row}",
                    "values": [[now_str]],
                })
                updated_count += 1
            else:
                # Append new listing
                max_sku_num += 1
                new_sku = f"SKU-{max_sku_num}"
                new_row_idx = len(all_rows) + len(new_rows) + 1
                row_data = [
                    new_sku,
                    item.lot_id or "",
                    item.title,
                    item.brand or "",
                    item.category or "",
                    f"=IFERROR(VLOOKUP(B{new_row_idx}, Inventory_Lots!A:F, 6, FALSE), 0)",
                    item.target_price or item.current_price,
                    item.current_price,
                    item.favorites,
                    item.views,
                    item.vinted_id,
                    item.vinted_url,
                    item.status or "Listed",
                    item.date_listed.strftime("%Y-%m-%d") if item.date_listed else datetime.now().strftime("%Y-%m-%d"),
                    now_str,
                ]
                new_rows.append(row_data)

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

        if new_rows:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")

        return {"updated": updated_count, "created": len(new_rows)}

    def sync_orders(self, scraped_orders: List[SalesOrder]) -> Dict[str, int]:
        """
        Inserts new sold orders into Sales_Log and updates matching Item_Listings status to 'Sold'.
        """
        sh = self.client.get_spreadsheet()
        ws_sales = sh.worksheet(TAB_SALES_LOG)
        ws_listings = sh.worksheet(TAB_ITEM_LISTINGS)

        sales_rows = ws_sales.get_all_values()
        existing_order_ids = set()
        if len(sales_rows) > 1:
            for row in sales_rows[1:]:
                if row and row[0].strip():
                    existing_order_ids.add(row[0].strip())

        # Map item titles to SKU, Brand, Category, Favorites in Item_Listings
        listings_rows = ws_listings.get_all_values()
        title_to_sku: Dict[str, str] = {}
        title_to_brand: Dict[str, str] = {}
        title_to_category: Dict[str, str] = {}
        title_to_favorites: Dict[str, int] = {}
        title_to_row_idx: Dict[str, int] = {}

        if len(listings_rows) > 1:
            headers = listings_rows[0]
            sku_idx = headers.index("Item_SKU") if "Item_SKU" in headers else 0
            title_idx = headers.index("Title") if "Title" in headers else 2
            brand_idx = headers.index("Brand") if "Brand" in headers else 3
            cat_idx = headers.index("Category") if "Category" in headers else 4
            fav_idx = headers.index("Favorites") if "Favorites" in headers else 8
            status_idx = headers.index("Status") if "Status" in headers else 12

            for r_idx, r in enumerate(listings_rows[1:], start=2):
                if len(r) > title_idx and r[title_idx].strip():
                    t_key = r[title_idx].strip().lower()
                    if len(r) > sku_idx:
                        title_to_sku[t_key] = r[sku_idx]
                        title_to_row_idx[t_key] = r_idx
                    if len(r) > brand_idx:
                        title_to_brand[t_key] = r[brand_idx]
                    if len(r) > cat_idx:
                        title_to_category[t_key] = r[cat_idx]
                    if len(r) > fav_idx:
                        try:
                            title_to_favorites[t_key] = int(r[fav_idx])
                        except ValueError:
                            pass

        new_sales_rows = []
        listing_status_updates = []

        for order in scraped_orders:
            if order.order_id.strip() in existing_order_ids:
                continue

            clean_title = order.item_title.strip().lower()
            matched_sku = order.item_sku or title_to_sku.get(clean_title, "")
            matched_brand = order.brand or title_to_brand.get(clean_title, "")
            matched_category = order.category or title_to_category.get(clean_title, "")
            matched_fav = order.favorites or title_to_favorites.get(clean_title, 0)
            next_row_num = len(sales_rows) + len(new_sales_rows) + 1

            new_sale_data = [
                order.order_id,
                matched_sku,
                order.item_title,
                matched_brand or f'=IFERROR(VLOOKUP(B{next_row_num}, Item_Listings!A:E, 4, FALSE), "")',
                matched_category or f'=IFERROR(VLOOKUP(B{next_row_num}, Item_Listings!A:E, 5, FALSE), "")',
                matched_fav or f'=IFERROR(VLOOKUP(B{next_row_num}, Item_Listings!A:I, 9, FALSE), 0)',
                order.sale_date.strftime("%Y-%m-%d") if order.sale_date else datetime.now().strftime("%Y-%m-%d"),
                order.sold_price,
                order.seller_fees,
                order.net_payout,
                f"=IFERROR(VLOOKUP(B{next_row_num}, Item_Listings!A:F, 6, FALSE), 0)",
                "=Shipping_Supplies!$H$2",
                f"=J{next_row_num}-K{next_row_num}-L{next_row_num}",
                f"=IF(H{next_row_num}>0, M{next_row_num}/H{next_row_num}, 0)",
                order.vinted_status or "Completed",
            ]
            new_sales_rows.append(new_sale_data)

            # If matched in listings, flag as Sold
            if clean_title in title_to_row_idx:
                row_in_listings = title_to_row_idx[clean_title]
                status_col_letter = chr(ord('A') + status_idx)
                listing_status_updates.append({
                    "range": f"{status_col_letter}{row_in_listings}",
                    "values": [["Sold"]],
                })


        if new_sales_rows:
            ws_sales.append_rows(new_sales_rows, value_input_option="USER_ENTERED")

        if listing_status_updates:
            ws_listings.batch_update(listing_status_updates, value_input_option="USER_ENTERED")

        return {"synced_orders": len(new_sales_rows)}
