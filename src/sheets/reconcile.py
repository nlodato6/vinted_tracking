"""
Plan-then-apply reconciliation between uploaded Vinted HTML exports and the Google Sheet.

`build_sync_plan` is a pure function: it takes the merged ImportSnapshot plus the current
sheet rows and returns a SyncPlan describing every change. The Sync page renders the plan
as the preview, and `SheetsSyncEngine.apply_sync_plan` writes exactly that plan, so the
preview numbers always equal the result.

Matching rules
- Items are matched by Vinted ID (Item_Listings/Hidden_Items: Vinted_ID or Vinted_URL;
  Sold_Items/Cancelled_Orders: Vinted_URL). Title matching is only a fallback for legacy rows
  without an ID, and only when the title is unique on both sides.
- Orders HTML has no item IDs or dates, so orders never create Sold rows. They are used to
  record cancellations; bundle contents are resolved later from email sync.
- A cancelled order is linked to a sold item with the same title when Vinted shows more sold
  items with that title than there are non-cancelled orders for it. The linked item is kept
  out of Sold_Items and its URL is stored on the Cancelled_Orders row, which makes repeat
  imports idempotent.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from src.html_parser import ImportSnapshot, parse_price
from src.models import ItemListing, SalesOrder
from src.sheets.schemas import (
    CANCELLED_ORDERS_HEADERS,
    LISTINGS_HEADERS,
    SOLD_ITEMS_HEADERS,
)

L = {h: i for i, h in enumerate(LISTINGS_HEADERS)}
S = {h: i for i, h in enumerate(SOLD_ITEMS_HEADERS)}
C = {h: i for i, h in enumerate(CANCELLED_ORDERS_HEADERS)}

_ITEM_ID_RE = re.compile(r"/items/(\d+)")


# ---------------------------------------------------------------------------
# Formula templates (row-relative)
# ---------------------------------------------------------------------------
def listing_formulas(n: int) -> Dict[int, str]:
    return {
        L["Unit_Cost"]: f"=IFERROR(VLOOKUP(B{n}, Inventory_Lots!A:F, 6, FALSE), 0)",
        L["Price_Lowered"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), "YES", "NO"))',
        L["Price_Drop"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), G{n}-H{n}, 0))',
    }


def sold_formulas(n: int) -> Dict[int, str]:
    return {
        S["Unit_Cost"]: f"=IFERROR(VLOOKUP(B{n}, Inventory_Lots!A:F, 6, FALSE), 0)",
        S["Price_Lowered"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), "YES", "NO"))',
        S["Price_Drop"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), G{n}-H{n}, 0))',
        S["Price_Discount"]: f'=IF(OR(G{n}="", L{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(L{n}), G{n}>L{n}), G{n}-L{n}, 0))',
        S["Sale_Type"]: f'=IF(OR(G{n}="", L{n}=""), "", IF(L{n}>=G{n}, "Original Price", IF(L{n}=H{n}, "Lowered Price", "Accepted Offer")))',
        S["Packaging_Cost"]: f'=IF(P{n}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)',
        S["Net_Profit"]: f"=IF(AND(ISNUMBER(L{n}), ISNUMBER(F{n}), ISNUMBER(Q{n})), L{n}-F{n}-Q{n}, IF(ISNUMBER(L{n}), L{n}, 0))",
        S["Margin_%"]: f"=IF(AND(ISNUMBER(L{n}), L{n}>0), R{n}/L{n}, 0)",
    }


def with_formulas(row: List[Any], n: int, formulas_fn) -> List[Any]:
    out = list(row)
    for idx, f in formulas_fn(n).items():
        out[idx] = f
    return out


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class SheetState:
    """Current data rows (header excluded) of each tab. Row i lives on sheet row i + 2."""

    listings: List[List[str]] = field(default_factory=list)
    sold: List[List[str]] = field(default_factory=list)
    hidden: List[List[str]] = field(default_factory=list)
    cancelled: List[List[str]] = field(default_factory=list)


@dataclass
class SyncPlan:
    # Writes
    final_listings: List[List[Any]] = field(default_factory=list)  # full Item_Listings body (formulas added on apply)
    listings_changed: bool = False
    sold_cell_updates: List[Tuple[int, int, Any]] = field(default_factory=list)  # (sheet_row, col_idx, value)
    sold_delete_rows: List[int] = field(default_factory=list)  # sheet row numbers
    sold_appends: List[List[Any]] = field(default_factory=list)
    hidden_delete_rows: List[int] = field(default_factory=list)
    hidden_appends: List[List[Any]] = field(default_factory=list)
    cancelled_appends: List[List[Any]] = field(default_factory=list)
    cancelled_cell_updates: List[Tuple[int, int, Any]] = field(default_factory=list)

    # Preview details
    new_listings: List[Dict[str, Any]] = field(default_factory=list)
    updated_listings: int = 0
    listing_to_sold: List[str] = field(default_factory=list)
    listing_to_hidden: List[str] = field(default_factory=list)
    hidden_to_listing: List[str] = field(default_factory=list)
    hidden_to_sold: List[str] = field(default_factory=list)
    new_sold: List[Dict[str, Any]] = field(default_factory=list)
    enriched_sold: int = 0
    new_hidden: List[str] = field(default_factory=list)
    new_cancelled: List[Dict[str, Any]] = field(default_factory=list)
    removed_for_cancellation: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    counts_before: Dict[str, int] = field(default_factory=dict)
    counts_after: Dict[str, int] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.listings_changed or self.sold_cell_updates or self.sold_delete_rows or self.sold_appends
            or self.hidden_delete_rows or self.hidden_appends or self.cancelled_appends or self.cancelled_cell_updates
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pad(row: List[Any], width: int) -> List[Any]:
    r = ["" if v is None else v for v in row[:width]]
    return r + [""] * (width - len(r))


def _blank(row: List[Any]) -> bool:
    return not any(str(c).strip() for c in row)


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").replace("\u2019", "'")).strip().lower()


def _vid_from_url(url: str) -> str:
    m = _ITEM_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _listing_vid(row: List[Any]) -> str:
    vid = str(row[L["Vinted_ID"]]).strip().replace(",", "")
    return vid if vid.isdigit() else _vid_from_url(str(row[L["Vinted_URL"]]))


def _item_url(vid: str) -> str:
    return f"https://www.vinted.com/items/{vid}"


def _is_bundle(title: str) -> bool:
    return bool(re.match(r"^bundle\b", (title or "").strip(), re.IGNORECASE))


def _max_sku(*tables: List[List[Any]]) -> int:
    best = 1000
    for rows in tables:
        for r in rows:
            m = re.match(r"^SKU-(\d+)$", str(r[0] if r else "").strip().upper())
            if m:
                best = max(best, int(m.group(1)))
    return best


def _count(rows: List[List[Any]]) -> int:
    return sum(1 for r in rows if not _blank(r))


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------
def _new_listing_row(item: ItemListing, sku: str, lot: str, title: str, now: datetime) -> List[Any]:
    row = [""] * len(LISTINGS_HEADERS)
    price = item.current_price or item.posted_price
    row[L["Item_SKU"]] = sku
    row[L["Lot_ID"]] = lot
    row[L["Title"]] = title
    row[L["Brand"]] = item.brand or ""
    row[L["Category"]] = item.category or ""
    row[L["Posted_Price"]] = item.posted_price or price
    row[L["Current_Price"]] = price
    row[L["Favorites"]] = item.favorites or 0
    row[L["Views"]] = item.views or 0
    row[L["Vinted_ID"]] = item.vinted_id
    row[L["Vinted_URL"]] = _item_url(item.vinted_id)
    row[L["Status"]] = item.status if item.status in ("Listed", "Reserved") else "Listed"
    row[L["Date_Listed"]] = now.strftime("%Y-%m-%d")
    row[L["Last_Synced"]] = now.strftime("%Y-%m-%d %H:%M:%S")
    row[L["Source"]] = item.source or "HTML"
    return row


def _sold_row_from_item(item: ItemListing, sku: str, lot: str) -> List[Any]:
    row = [""] * len(SOLD_ITEMS_HEADERS)
    price = item.sold_price or item.current_price
    row[S["Item_SKU"]] = sku
    row[S["Lot_ID"]] = lot
    row[S["Title"]] = item.title
    row[S["Brand"]] = item.brand or ""
    row[S["Category"]] = item.category or ""
    row[S["Posted_Price"]] = item.posted_price or price
    row[S["Current_Price"]] = item.current_price or price
    row[S["Sold_Price"]] = price
    row[S["Sale_Date"]] = ""  # orders HTML has no dates; email sync fills this in
    row[S["Platform"]] = "Vinted"
    row[S["Favorites"]] = item.favorites or 0
    row[S["Views"]] = item.views or 0
    row[S["Vinted_URL"]] = _item_url(item.vinted_id)
    row[S["Status"]] = "Sold"
    row[S["Source"]] = item.source or "HTML"
    return row


def _sold_row_from_listing(lrow: List[Any], item: Optional[ItemListing], default_lot: str) -> List[Any]:
    """Moves an Item_Listings / Hidden_Items row into the Sold_Items layout, keeping SKU, lot and price history."""
    row = [""] * len(SOLD_ITEMS_HEADERS)
    vid = _listing_vid(lrow)
    posted = lrow[L["Posted_Price"]]
    current = lrow[L["Current_Price"]]
    sold_price = (item.sold_price or item.current_price) if item else 0
    row[S["Item_SKU"]] = lrow[L["Item_SKU"]]
    row[S["Lot_ID"]] = lrow[L["Lot_ID"]] or default_lot
    row[S["Title"]] = lrow[L["Title"]]
    row[S["Brand"]] = lrow[L["Brand"]] or (item.brand if item else "")
    row[S["Category"]] = lrow[L["Category"]] or (item.category if item else "")
    row[S["Posted_Price"]] = posted
    row[S["Current_Price"]] = current
    row[S["Price_History"]] = lrow[L["Price_History"]]
    row[S["Sold_Price"]] = sold_price or parse_price(current) or parse_price(posted)
    row[S["Sale_Date"]] = ""
    row[S["Platform"]] = "Vinted"
    row[S["Favorites"]] = max(int(parse_price(lrow[L["Favorites"]])), item.favorites if item else 0)
    row[S["Views"]] = max(int(parse_price(lrow[L["Views"]])), item.views if item else 0)
    row[S["Vinted_URL"]] = _item_url(vid) if vid else lrow[L["Vinted_URL"]]
    row[S["Status"]] = "Sold"
    row[S["Date_Listed"]] = lrow[L["Date_Listed"]]
    row[S["Source"]] = lrow[L["Source"]] or "HTML"
    return row


def _updated_listing_row(lrow: List[Any], item: ItemListing, now: datetime, status: str) -> Tuple[List[Any], bool]:
    row = list(lrow)
    changed = False
    old_price = parse_price(row[L["Current_Price"]])
    new_price = item.current_price
    if new_price and abs(old_price - new_price) > 0.001:
        if old_price:
            entry = f"{now.strftime('%m/%d')}: ${old_price:.2f} \u2192 ${new_price:.2f}"
            hist = str(row[L["Price_History"]]).strip()
            row[L["Price_History"]] = f"{hist}; {entry}" if hist else entry
        row[L["Current_Price"]] = new_price
        if not parse_price(row[L["Posted_Price"]]):
            row[L["Posted_Price"]] = new_price
        changed = True
    for col, val in (("Favorites", item.favorites), ("Views", item.views)):
        if val and int(parse_price(row[L[col]])) != val:
            row[L[col]] = val
            changed = True
    for col, val in (("Brand", item.brand), ("Category", item.category)):
        if val and not str(row[L[col]]).strip():
            row[L[col]] = val
            changed = True
    if not str(row[L["Vinted_ID"]]).strip():
        row[L["Vinted_ID"]] = item.vinted_id
        changed = True
    if not str(row[L["Vinted_URL"]]).strip():
        row[L["Vinted_URL"]] = _item_url(item.vinted_id)
        changed = True
    if str(row[L["Status"]]).strip() != status:
        row[L["Status"]] = status
        changed = True
    if changed:
        row[L["Last_Synced"]] = now.strftime("%Y-%m-%d %H:%M:%S")
    return row, changed


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------
def build_sync_plan(
    snap: ImportSnapshot,
    state: SheetState,
    default_lot: str = "",
    listing_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    now: Optional[datetime] = None,
) -> SyncPlan:
    now = now or datetime.now()
    overrides = listing_overrides or {}
    plan = SyncPlan()

    listings = [_pad(r, len(LISTINGS_HEADERS)) for r in state.listings]
    sold = [_pad(r, len(SOLD_ITEMS_HEADERS)) for r in state.sold]
    hidden = [_pad(r, len(LISTINGS_HEADERS)) for r in state.hidden]
    cancelled = [_pad(r, len(CANCELLED_ORDERS_HEADERS)) for r in state.cancelled]

    plan.counts_before = {
        "active": _count(listings), "sold": _count(sold), "hidden": _count(hidden), "cancelled": _count(cancelled),
    }

    # ---- Index current sheet by Vinted ID -------------------------------
    sold_by_vid: Dict[str, int] = {}
    sold_noid_by_title: Dict[str, List[int]] = {}
    for i, r in enumerate(sold):
        if _blank(r):
            continue
        vid = _vid_from_url(str(r[S["Vinted_URL"]]))
        if vid:
            sold_by_vid.setdefault(vid, i)
        else:
            sold_noid_by_title.setdefault(_norm(r[S["Title"]]), []).append(i)

    hidden_by_vid: Dict[str, int] = {}
    for i, r in enumerate(hidden):
        vid = _listing_vid(r) if not _blank(r) else ""
        if vid:
            hidden_by_vid.setdefault(vid, i)

    cancelled_vids: Set[str] = {_vid_from_url(str(r[C["Vinted_URL"]])) for r in cancelled} - {""}
    cancelled_order_ids: Set[str] = {str(r[C["Order_ID"]]).strip() for r in cancelled} - {""}

    snap_titles = Counter(_norm(i.title) for i in snap.items.values())
    snap_by_title: Dict[str, ItemListing] = {_norm(i.title): i for i in snap.items.values()}

    # ---- 0. Cancellations -------------------------------------------------
    sold_delete: Set[int] = set()
    valid_counts = Counter(_norm(o.item_title) for o in snap.valid_orders)
    have_orders = bool(snap.orders)

    def _link_candidates(t: str) -> Dict[str, Optional[int]]:
        cand: Dict[str, Optional[int]] = {}
        for it in snap.items.values():
            if it.status == "Sold" and _norm(it.title) == t and it.vinted_id not in cancelled_vids:
                cand[it.vinted_id] = sold_by_vid.get(it.vinted_id)
        for vid, idx in sold_by_vid.items():
            if vid not in cancelled_vids and idx not in sold_delete and _norm(sold[idx][S["Title"]]) == t:
                cand.setdefault(vid, idx)
        return cand

    def _pick(title: str) -> Tuple[str, Optional[int]]:
        """Returns (vinted_id, sold_row_index) of the sold item a cancelled order refers to, or ('', None)."""
        if _is_bundle(title):
            return "", None
        t = _norm(title)
        cand = _link_candidates(t)
        if not cand:
            return "", None
        if have_orders:
            if len(cand) - valid_counts.get(t, 0) <= 0:
                return "", None
        elif len(cand) > 1:
            plan.warnings.append(
                f"Cancelled order '{title}' matches {len(cand)} sold items; upload My Orders together with your Sold closet to link it"
            )
            return "", None
        vid = max(cand, key=lambda v: int(v))
        return vid, cand[vid]

    def _mark_cancelled(vid: str, idx: Optional[int]) -> None:
        cancelled_vids.add(vid)
        if idx is not None:
            sold_delete.add(idx)
            plan.removed_for_cancellation.append(sold[idx][S["Title"]])

    # 0a. Cancelled rows recorded earlier without a linked item (e.g. My Orders uploaded before the Sold closet)
    for ci, crow_existing in enumerate(cancelled):
        if _blank(crow_existing) or _vid_from_url(str(crow_existing[C["Vinted_URL"]])):
            continue
        vid, idx = _pick(str(crow_existing[C["Item_Title"]]))
        if vid:
            plan.cancelled_cell_updates.append((ci + 2, C["Vinted_URL"], _item_url(vid)))
            if idx is not None and not str(crow_existing[C["Item_SKU"]]).strip():
                plan.cancelled_cell_updates.append((ci + 2, C["Item_SKU"], sold[idx][S["Item_SKU"]]))
            _mark_cancelled(vid, idx)

    # 0b. New cancelled orders from this upload
    for co in snap.cancelled_orders:
        oid = (co.order_id or "").strip()
        if oid and oid in cancelled_order_ids:
            continue
        matched_vid, matched_sold_idx = _pick(co.item_title)
        item = snap.items.get(matched_vid) if matched_vid else None
        sold_row = sold[matched_sold_idx] if matched_sold_idx is not None else None
        crow = [""] * len(CANCELLED_ORDERS_HEADERS)
        crow[C["Order_ID"]] = oid
        crow[C["Item_SKU"]] = sold_row[S["Item_SKU"]] if sold_row else ""
        crow[C["Item_Title"]] = co.item_title
        crow[C["Brand"]] = co.brand or (item.brand if item else "")
        crow[C["Category"]] = co.category or (item.category if item else "")
        crow[C["Lost_Revenue"]] = co.sold_price
        crow[C["Original_Price"]] = (item.current_price if item else 0) or co.sold_price
        crow[C["Cancellation_Date"]] = ""  # not in the HTML; email sync fills this in
        crow[C["Status_Details"]] = getattr(co, "cancellation_reason", "") or "Order cancelled"
        crow[C["Buyer_Username"]] = getattr(co, "buyer_username", "") or ""
        crow[C["Vinted_URL"]] = _item_url(matched_vid) if matched_vid else (f"https://www.vinted.com/inbox/{oid}" if oid else "")
        crow[C["Source"]] = "HTML"
        plan.cancelled_appends.append(crow)
        if oid:
            cancelled_order_ids.add(oid)
        if matched_vid:
            _mark_cancelled(matched_vid, matched_sold_idx)
        plan.new_cancelled.append({
            "Order ID": oid,
            "Title": co.item_title,
            "Price": co.sold_price,
            "Linked Item": (item.title if item else (sold_row[S["Title"]] if sold_row else "")) or ("Bundle - resolved by email sync" if _is_bundle(co.item_title) else "Not found in closet"),
        })

    # ---- 1. Item_Listings -------------------------------------------------
    max_sku = _max_sku(listings, sold, hidden)
    in_listings: Set[str] = set()
    hidden_delete: Set[int] = set()
    final_listings: List[List[Any]] = []
    moved_to_sold_vids: Set[str] = set()

    noid_listing_titles = Counter(_norm(r[L["Title"]]) for r in listings if not _blank(r) and not _listing_vid(r))
    for r in listings:
        if _blank(r):
            plan.listings_changed = True
            continue
        vid = _listing_vid(r)
        if not vid:
            t = _norm(r[L["Title"]])
            cand = snap_by_title.get(t)
            if cand and snap_titles[t] == 1 and noid_listing_titles[t] == 1 and cand.vinted_id not in in_listings:
                vid = cand.vinted_id
        item = snap.items.get(vid) if vid else None

        if vid and vid in in_listings:  # duplicate row for the same Vinted item
            plan.listings_changed = True
            plan.warnings.append(f"Removed duplicate active row for '{r[L['Title']]}'")
            continue

        if item is None:
            if vid and snap.has_active_source:
                plan.listings_changed = True
                if vid in sold_by_vid or vid in cancelled_vids:
                    continue  # already recorded elsewhere; drop the stale active row
                hrow = list(r)
                hrow[L["Status"]] = "Hidden"
                plan.hidden_appends.append(hrow)
                plan.listing_to_hidden.append(r[L["Title"]])
                continue
            final_listings.append(r)
            if vid:
                in_listings.add(vid)
            continue

        if item.status == "Sold":
            plan.listings_changed = True
            if vid in cancelled_vids:
                continue
            if vid not in sold_by_vid:
                plan.sold_appends.append(_sold_row_from_listing(r, item, default_lot))
                plan.listing_to_sold.append(r[L["Title"]])
            moved_to_sold_vids.add(vid)
            continue
        if item.status == "Hidden":
            plan.listings_changed = True
            if vid not in hidden_by_vid:
                hrow = list(r)
                hrow[L["Status"]] = "Hidden"
                plan.hidden_appends.append(hrow)
            plan.listing_to_hidden.append(r[L["Title"]])
            continue

        new_row, changed = _updated_listing_row(r, item, now, item.status)
        if changed:
            plan.listings_changed = True
            plan.updated_listings += 1
        final_listings.append(new_row)
        in_listings.add(vid)

    for item in snap.by_status("Listed", "Reserved"):
        vid = item.vinted_id
        if vid in in_listings:
            continue
        if vid in sold_by_vid and vid not in cancelled_vids:
            plan.warnings.append(f"'{item.title}' is listed on Vinted but already recorded as sold; left in Sold_Items")
            continue
        if vid in hidden_by_vid:
            hrow = list(hidden[hidden_by_vid[vid]])
            new_row, _ = _updated_listing_row(hrow, item, now, item.status)
            final_listings.append(new_row)
            hidden_delete.add(hidden_by_vid[vid])
            plan.hidden_to_listing.append(item.title)
        else:
            ov = overrides.get(vid, {})
            max_sku += 1
            lot = ov.get("lot", item.lot_id or default_lot) if "lot" in ov else (item.lot_id or default_lot)
            title = (ov.get("title") or item.title).strip()
            final_listings.append(_new_listing_row(item, f"SKU-{max_sku}", lot, title, now))
            plan.new_listings.append({"Vinted ID": vid, "Title": title, "Brand": item.brand or "", "Price": item.current_price, "Category": item.category or "", "Assigned Lot": lot})
        in_listings.add(vid)
        plan.listings_changed = True

    plan.final_listings = final_listings

    # ---- 2. Sold_Items ----------------------------------------------------
    sold_items = sorted(snap.by_status("Sold"), key=lambda i: int(i.vinted_id))
    sold_titles_in_snap = Counter(_norm(i.title) for i in sold_items)
    for item in sold_items:
        vid = item.vinted_id
        if vid in cancelled_vids or vid in moved_to_sold_vids:
            continue
        idx = sold_by_vid.get(vid)
        if idx is None:
            t = _norm(item.title)
            noid = [i for i in sold_noid_by_title.get(t, []) if i not in sold_delete]
            if len(noid) == 1 and sold_titles_in_snap[t] == 1:
                idx = noid[0]
                sold_noid_by_title[t] = []
                plan.sold_cell_updates.append((idx + 2, S["Vinted_URL"], _item_url(vid)))
        if idx is not None:
            row = sold[idx]
            n_before = len(plan.sold_cell_updates)
            for col, val in (("Favorites", item.favorites), ("Views", item.views)):
                if val and val > int(parse_price(row[S[col]])):
                    plan.sold_cell_updates.append((idx + 2, S[col], val))
            for col, val in (("Brand", item.brand), ("Category", item.category)):
                if val and not str(row[S[col]]).strip():
                    plan.sold_cell_updates.append((idx + 2, S[col], val))
            price = item.sold_price or item.current_price
            if price and not parse_price(row[S["Sold_Price"]]):
                plan.sold_cell_updates.append((idx + 2, S["Sold_Price"], price))
            if len(plan.sold_cell_updates) > n_before:
                plan.enriched_sold += 1
            continue
        if vid in hidden_by_vid:
            hidx = hidden_by_vid[vid]
            plan.sold_appends.append(_sold_row_from_listing(hidden[hidx], item, default_lot))
            hidden_delete.add(hidx)
            plan.hidden_to_sold.append(item.title)
            continue
        max_sku += 1
        lot = item.lot_id or default_lot
        plan.sold_appends.append(_sold_row_from_item(item, f"SKU-{max_sku}", lot))
        plan.new_sold.append({"Vinted ID": vid, "Title": item.title, "Brand": item.brand or "", "Price": item.sold_price or item.current_price, "Category": item.category or "", "Lot": lot})

    # ---- 3. Hidden_Items --------------------------------------------------
    pending_hidden_vids = {_listing_vid(r) for r in plan.hidden_appends}
    for item in snap.by_status("Hidden"):
        vid = item.vinted_id
        if vid in hidden_by_vid or vid in pending_hidden_vids or vid in sold_by_vid:
            continue
        max_sku += 1
        hrow = _new_listing_row(item, f"SKU-{max_sku}", item.lot_id or default_lot, item.title, now)
        hrow[L["Status"]] = "Hidden"
        plan.hidden_appends.append(hrow)
        plan.new_hidden.append(item.title)

    plan.sold_delete_rows = sorted((i + 2 for i in sold_delete), reverse=True)
    plan.hidden_delete_rows = sorted((i + 2 for i in hidden_delete), reverse=True)

    if snap.orders and not snap.has_sold_source:
        plan.warnings.append("Orders were used for cancellations only. Upload your Sold closet export to add sold items.")
    for title, lower, winner in snap.status_conflicts:
        plan.warnings.append(f"'{title}' appeared as {lower} and {winner} in different files; kept {winner}")

    plan.counts_after = {
        "active": len(final_listings),
        "sold": plan.counts_before["sold"] - len(sold_delete) + len(plan.sold_appends),
        "hidden": plan.counts_before["hidden"] - len(hidden_delete) + len(plan.hidden_appends),
        "cancelled": plan.counts_before["cancelled"] + len(plan.cancelled_appends),
    }
    return plan
