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
    TAB_ITEM_LISTINGS,
    TAB_HIDDEN_ITEMS,
    TAB_SOLD_ITEMS,
    TAB_CANCELLED_ORDERS,
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


def sale_type_formula(n: int) -> str:
    """Rows that share an Order_ID (V) are items of one bundle order and show 'Bundle'."""
    return (
        f'=IF(AND(V{n}<>"", COUNTIF(V:V, V{n})>1), "Bundle", '
        f'IF(OR(G{n}="", L{n}=""), "", IF(L{n}>=G{n}, "Original Price", IF(L{n}=H{n}, "Lowered Price", "Accepted Offer"))))'
    )


def packaging_formula(n: int) -> str:
    """One package per order: only the first row of a bundle (shared Order_ID) carries the packaging cost."""
    return (
        f'=IF(P{n}="FB Marketplace (Local)", 0, '
        f'IF(AND(V{n}<>"", COUNTIF(V$1:V{n}, V{n})>1), 0, Shipping_Supplies!$H$2))'
    )


def sold_formulas(n: int) -> Dict[int, str]:
    return {
        S["Unit_Cost"]: f"=IFERROR(VLOOKUP(B{n}, Inventory_Lots!A:F, 6, FALSE), 0)",
        S["Price_Lowered"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), "YES", "NO"))',
        S["Price_Drop"]: f'=IF(OR(G{n}="", H{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(H{n}), H{n}<G{n}), G{n}-H{n}, 0))',
        S["Price_Discount"]: f'=IF(OR(G{n}="", L{n}=""), "", IF(AND(ISNUMBER(G{n}), ISNUMBER(L{n}), G{n}>L{n}), G{n}-L{n}, 0))',
        S["Sale_Type"]: sale_type_formula(n),
        S["Packaging_Cost"]: packaging_formula(n),
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

    # Email sync preview details
    email_dated: List[Dict[str, Any]] = field(default_factory=list)
    email_bundles: List[Dict[str, Any]] = field(default_factory=list)
    email_cancellations: List[Dict[str, Any]] = field(default_factory=list)
    email_offers: List[Dict[str, Any]] = field(default_factory=list)
    email_unmatched: List[Dict[str, Any]] = field(default_factory=list)
    email_counts: Dict[str, int] = field(default_factory=dict)

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
    archive_unlisted: bool = False,
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
    sold_delete: Set[int] = set()
    sold_by_vid: Dict[str, int] = {}
    sold_noid_by_title: Dict[str, List[int]] = {}
    for i, r in enumerate(sold):
        if _blank(r):
            continue
        vid = _vid_from_url(str(r[S["Vinted_URL"]]))
        if vid:
            if vid in sold_by_vid and snap.has_sold_source:
                sold_delete.add(i)
                plan.warnings.append(f"Removed duplicate sold row for '{r[S['Title']]}'")
            else:
                sold_by_vid.setdefault(vid, i)
        else:
            sold_noid_by_title.setdefault(_norm(r[S["Title"]]), []).append(i)

    hidden_by_vid: Dict[str, int] = {}
    for i, r in enumerate(hidden):
        vid = _listing_vid(r) if not _blank(r) else ""
        if vid:
            hidden_by_vid.setdefault(vid, i)

    cancelled_vids: Set[str] = {v for r in cancelled for v in _ITEM_ID_RE.findall(str(r[C["Vinted_URL"]]))}
    cancelled_order_ids: Set[str] = {str(r[C["Order_ID"]]).strip() for r in cancelled} - {""}

    snap_titles = Counter(_norm(i.title) for i in snap.items.values())
    snap_by_title: Dict[str, ItemListing] = {_norm(i.title): i for i in snap.items.values()}

    # ---- 0. Cancellations -------------------------------------------------
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
            if vid and (vid in sold_by_vid or vid in cancelled_vids):
                plan.listings_changed = True
                continue  # already recorded elsewhere; drop the stale active row
            if vid and snap.has_active_source and archive_unlisted:
                plan.listings_changed = True
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

    snap_sold_vids: Set[str] = {i.vinted_id for i in snap.by_status("Sold")}
    for item in snap.by_status("Listed", "Reserved"):
        vid = item.vinted_id
        if vid in in_listings:
            continue
        if vid in sold_by_vid and vid not in cancelled_vids:
            if snap.has_active_source and snap.has_sold_source and vid not in snap_sold_vids:
                sidx = sold_by_vid[vid]
                sold_delete.add(sidx)
                srow = list(sold[sidx])
                ov = overrides.get(vid, {})
                lot = ov.get("lot", srow[S["Lot_ID"]] or item.lot_id or default_lot)
                title = (ov.get("title") or item.title or srow[S["Title"]]).strip()
                max_sku += 1
                new_row = _new_listing_row(item, srow[S["Item_SKU"]] or f"SKU-{max_sku}", lot, title, now)
                final_listings.append(new_row)
                in_listings.add(vid)
                plan.listings_changed = True
                plan.new_listings.append({"Vinted ID": vid, "Title": title, "Brand": item.brand or "", "Price": item.current_price, "Category": item.category or "", "Assigned Lot": lot})
                plan.warnings.append(f"'{item.title}' was recorded in Sold_Items but is actively listed on Vinted; moved to Item_Listings")
                continue
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

    if snap.has_sold_source:
        for t, row_indices in sold_noid_by_title.items():
            valid_snap_count = sold_titles_in_snap.get(t, 0)
            have_vid_count = sum(1 for v in sold_by_vid if _norm(snap.items[v].title if v in snap.items else "") == t)
            excess = (len(row_indices) + have_vid_count) - valid_snap_count
            if excess > 0:
                for r_idx in row_indices[:excess]:
                    if r_idx not in sold_delete:
                        sold_delete.add(r_idx)
                        plan.warnings.append(f"Removed duplicate placeholder sold row for '{sold[r_idx][S['Title']]}'")

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


# ---------------------------------------------------------------------------
# Email sync planner
# ---------------------------------------------------------------------------
def split_bundle_total(total: float, weights: List[float]) -> List[float]:
    """Splits an order total across its items in proportion to their listed prices (evenly if any is unknown).
    Shares are rounded to cents and always add up exactly to the total."""
    n = len(weights)
    if n == 0:
        return []
    if any((w or 0) <= 0 for w in weights):
        weights = [1.0] * n
    tot_w = float(sum(weights))
    shares = [round(total * w / tot_w, 2) for w in weights]
    shares[-1] = round(shares[-1] + round(total - sum(shares), 2), 2)
    return shares


def _event_items(ev: Any) -> List[str]:
    items = [str(t).strip() for t in (getattr(ev, "items", None) or []) if str(t).strip()]
    return items or ([ev.item_title] if getattr(ev, "item_title", "") else [])


def build_email_plan(events: List[Any], state: SheetState, default_lot: str = "", now: Optional[datetime] = None) -> SyncPlan:
    """
    Pure planner for parsed Vinted emails (src.email_parser.ParsedEmailEvent).

    - Shipping-label emails are the order record: one per order, with the Transaction ID and every
      item title (bundles list all items). Each item is matched to a Sold_Items row by title; the row
      gets Sale_Date (label date), Order_ID (Transaction ID) and, when a 'completed' / 'You sold' email
      gives the price, Sold_Price (bundle totals are split across items by listed price).
      Rows that already carry the Transaction ID are reused, so re-running is idempotent.
    - Items of a bundle share the Order_ID; the Sale_Type formula then shows "Bundle" and only the
      first row carries the packaging cost.
    - Sold items still on Item_Listings are moved to Sold_Items. Nothing else creates sold rows.
    - Cancellation emails are matched to Cancelled_Orders by Vinted conversation ID (the same ID the
      My Orders HTML import stores), then by title. Existing records get their Cancellation_Date;
      new cancellations move the item out of Sold_Items.
    - Offers are appended to the listing's Price_History once (duplicates are skipped).
    """
    now = now or datetime.now()
    plan = SyncPlan()
    listings = [_pad(r, len(LISTINGS_HEADERS)) for r in state.listings]
    sold = [_pad(r, len(SOLD_ITEMS_HEADERS)) for r in state.sold]
    hidden = [_pad(r, len(LISTINGS_HEADERS)) for r in state.hidden]
    cancelled = [_pad(r, len(CANCELLED_ORDERS_HEADERS)) for r in state.cancelled]
    plan.counts_before = {
        "active": _count(listings), "sold": _count(sold), "hidden": _count(hidden), "cancelled": _count(cancelled),
    }
    O = S["Order_ID"]

    # ---- Normalise events -------------------------------------------------
    seen: Set[Any] = set()
    evs: List[Any] = []
    for ev in sorted(events, key=lambda e: e.timestamp):
        key = getattr(ev, "message_id", "") or (ev.event_type, ev.raw_subject, ev.timestamp.isoformat())
        if key not in seen:
            seen.add(key)
            evs.append(ev)

    labels: Dict[str, Any] = {}
    for ev in evs:
        if ev.event_type in ("sale", "bundle_sale"):
            txn = str(getattr(ev, "transaction_id", "") or ev.order_id or "").strip()
            if txn and txn not in labels and _event_items(ev):
                labels[txn] = ev
    labels_by_date = sorted(labels.items(), key=lambda kv: kv[1].timestamp)

    price_by_txn: Dict[str, float] = {}
    for ev in evs:
        txn = str(getattr(ev, "transaction_id", "") or "").strip()
        if ev.event_type == "completed" and txn and ev.sold_price:
            price_by_txn[txn] = ev.sold_price
    used_notice_txns: Set[str] = set()
    for ev in evs:  # "You sold" notices carry the price of single-item orders but no Transaction ID
        if ev.event_type != "sale_notice" or not ev.sold_price:
            continue
        t = _norm(ev.item_title)
        for txn, lab in labels_by_date:
            items = _event_items(lab)
            if (
                txn not in used_notice_txns and len(items) == 1 and _norm(items[0]) == t
                and -1 <= (lab.timestamp - ev.timestamp).total_seconds() / 86400 <= 10
            ):
                price_by_txn.setdefault(txn, ev.sold_price)
                used_notice_txns.add(txn)
                break

    cancels = [ev for ev in evs if ev.event_type == "cancellation"]
    plan.email_counts = {
        "orders": len(labels),
        "bundles": sum(1 for lab in labels.values() if len(_event_items(lab)) > 1),
        "items": sum(len(_event_items(lab)) for lab in labels.values()),
        "cancellations": len(cancels),
        "prices": len(price_by_txn),
        "offers": sum(1 for ev in evs if ev.event_type == "offer"),
        "favorites": sum(1 for ev in evs if ev.event_type == "favorite"),
        "shipped": sum(1 for ev in evs if ev.event_type == "order_update" and ev.status == "shipped"),
        "delivered": sum(1 for ev in evs if ev.event_type == "order_update" and ev.status == "delivered"),
    }

    # ---- Which order did each cancellation email refer to? ------------------
    by_first_item: Dict[str, List[str]] = {}
    for txn, lab in labels_by_date:
        by_first_item.setdefault(_norm(_event_items(lab)[0]), []).append(txn)
    cancel_txn: Dict[int, str] = {}
    for ci, c in enumerate(cancels):
        cands = [
            t for t in by_first_item.get(_norm(c.item_title), [])
            if labels[t].timestamp <= c.timestamp and t not in cancel_txn.values()
        ]
        if cands:
            cancel_txn[ci] = cands[-1]
    cancelled_txns = set(cancel_txn.values())

    # ---- Index the sheet ------------------------------------------------------
    def _vid_key(i: int) -> Tuple[int, int]:
        v = _vid_from_url(str(sold[i][S["Vinted_URL"]]))
        return (0, int(v)) if v else (1, i)

    sold_by_title: Dict[str, List[int]] = {}
    for i, r in enumerate(sold):
        if not _blank(r):
            sold_by_title.setdefault(_norm(r[S["Title"]]), []).append(i)
    for rows in sold_by_title.values():
        rows.sort(key=_vid_key)
    claimed: Dict[int, str] = {i: str(r[O]).strip() for i, r in enumerate(sold) if str(r[O]).strip() in labels}
    used_rows: Set[int] = set()
    sold_delete: Set[int] = set()

    listing_by_title: Dict[str, List[int]] = {}
    listing_by_vid: Dict[str, int] = {}
    for i, r in enumerate(listings):
        if _blank(r):
            continue
        listing_by_title.setdefault(_norm(r[L["Title"]]), []).append(i)
        vid = _listing_vid(r)
        if vid:
            listing_by_vid.setdefault(vid, i)
    listing_moved: Set[int] = set()

    def take_sold(title: str, txn: str) -> Optional[int]:
        rows = sold_by_title.get(_norm(title), [])
        for i in rows:
            if i not in used_rows and claimed.get(i) == txn:
                used_rows.add(i)
                return i
        for i in rows:
            if i not in used_rows and i not in claimed and not str(sold[i][O]).strip():
                claimed[i] = txn
                used_rows.add(i)
                return i
        return None

    def take_listing(title: str) -> Optional[int]:
        rows = [i for i in listing_by_title.get(_norm(title), []) if i not in listing_moved]
        return rows[0] if rows else None

    def sold_update(i: int, col: str, value: Any) -> None:
        plan.sold_cell_updates.append((i + 2, S[col], value))

    # ---- Cancellation records already on the sheet --------------------------------
    cancel_by_oid: Dict[str, int] = {}
    for i, r in enumerate(cancelled):
        oid = str(r[C["Order_ID"]]).strip()
        if oid and not _blank(r):
            cancel_by_oid.setdefault(oid, i)
    used_cancel_rows: Set[int] = set()

    def find_cancel_row(c: Any, txn: str, items: List[str]) -> Optional[int]:
        oid = (c.order_id or "").strip()
        if oid and oid in cancel_by_oid:
            return cancel_by_oid[oid]
        if txn:
            for i, r in enumerate(cancelled):
                if not _blank(r) and txn in str(r[C["Status_Details"]]):
                    return i
        t, n = _norm(items[0]), len(items)
        for i, r in enumerate(cancelled):
            if i in used_cancel_rows or _blank(r) or str(r[C["Cancellation_Date"]]).strip():
                continue
            rt = _norm(r[C["Item_Title"]])
            mb = re.match(r"^bundle (\d+) items", rt)
            if (n > 1 and mb and int(mb.group(1)) == n) or rt == t:
                return i
        return None

    # How many of each cancelled order's items still sit in Sold_Items and must be moved out?
    cancel_info: Dict[int, Dict[str, Any]] = {}
    need_by_txn: Dict[str, int] = {}
    for ci, c in enumerate(cancels):
        txn = cancel_txn.get(ci, "")
        items = _event_items(labels[txn]) if txn else _event_items(c)
        ri = find_cancel_row(c, txn, items) if items else None
        if ri is not None:
            used_cancel_rows.add(ri)
            r = cancelled[ri]
            done = bool(txn) and txn in str(r[C["Status_Details"]])
            need = 0 if done else max(0, len(items) - len(_ITEM_ID_RE.findall(str(r[C["Vinted_URL"]]))))
        else:
            need = len(items)
        cancel_info[ci] = {"txn": txn, "items": items, "row": ri, "need": need}
        if txn:
            need_by_txn[txn] = need
    cancel_claims: Dict[str, List[int]] = {}

    # ---- 1. Orders -> Sold_Items ------------------------------------------------
    for txn, lab in labels_by_date:
        if txn in cancelled_txns:
            # Claim the cancelled order's own rows in date order, so a later re-sale of the same
            # items (e.g. a cancelled bundle bought again) keeps the other rows.
            claims = cancel_claims.setdefault(txn, [])
            for title in _event_items(lab):
                if len(claims) >= need_by_txn.get(txn, 0):
                    break
                idx = take_sold(title, txn)
                if idx is not None:
                    claims.append(idx)
            continue
        items = _event_items(lab)
        sale_date = lab.timestamp.strftime("%Y-%m-%d")
        bundle = len(items) > 1
        targets: List[Tuple[str, int, str]] = []  # (kind, index, title)
        for title in items:
            idx = take_sold(title, txn)
            if idx is not None:
                targets.append(("row", idx, title))
                continue
            li = take_listing(title)
            if li is not None:
                listing_moved.add(li)
                srow = _sold_row_from_listing(listings[li], None, default_lot)
                srow[S["Sale_Date"]] = sale_date
                srow[S["Order_ID"]] = txn
                plan.sold_appends.append(srow)
                plan.listing_to_sold.append(title)
                targets.append(("new", len(plan.sold_appends) - 1, title))
                continue
            plan.email_unmatched.append({
                "Transaction": txn, "Date": sale_date, "Title": title,
                "Reason": "No matching row in Sold_Items or Item_Listings (import your Sold closet HTML first)",
            })

        def _row(kind: str, k: int) -> List[Any]:
            return sold[k] if kind == "row" else plan.sold_appends[k]

        total = price_by_txn.get(txn, 0.0)
        shares: List[Optional[float]] = [None] * len(targets)
        if total and targets:
            weights = [
                parse_price(_row(kind, k)[S["Posted_Price"]]) or parse_price(_row(kind, k)[S["Sold_Price"]])
                for kind, k, _ in targets
            ]
            shares = list(split_bundle_total(total, weights)) if len(targets) == len(items) else shares
            if not bundle:
                shares = [total]

        for (kind, k, title), share in zip(targets, shares):
            row = _row(kind, k)
            if kind == "new":
                if share is not None:
                    row[S["Sold_Price"]] = share
                plan.email_dated.append({
                    "Title": title, "Sale Date": sale_date, "Transaction": txn, "Bundle": "Yes" if bundle else "",
                    "Sold Price": share if share is not None else row[S["Sold_Price"]], "Change": "Moved from active listings",
                })
                continue
            changes = []
            if not str(row[S["Sale_Date"]]).strip():
                sold_update(k, "Sale_Date", sale_date)
                changes.append("sale date")
            if str(row[O]).strip() != txn:
                sold_update(k, "Order_ID", txn)
                changes.append("order ID")
                if bundle:  # (re)write the bundle-aware formulas once, when the row joins its bundle
                    sold_update(k, "Sale_Type", sale_type_formula(k + 2))
                    sold_update(k, "Packaging_Cost", packaging_formula(k + 2))
            if share is not None and abs(parse_price(row[S["Sold_Price"]]) - share) > 0.004:
                sold_update(k, "Sold_Price", share)
                changes.append("price")
            if changes:
                plan.email_dated.append({
                    "Title": title, "Sale Date": sale_date, "Transaction": txn, "Bundle": "Yes" if bundle else "",
                    "Sold Price": share if share is not None else row[S["Sold_Price"]], "Change": ", ".join(changes),
                })
        if bundle:
            plan.email_bundles.append({
                "Transaction": txn, "Date": sale_date, "Items": len(items), "Titles": ", ".join(items),
                "Order Total": f"${total:.2f}" if total else "Not in email (prices kept)", "Matched": f"{len(targets)}/{len(items)}",
            })

    # ---- 2. Cancellations -------------------------------------------------------
    new_cancel_oids: Set[str] = set()

    def move_out(rows: List[int]) -> List[int]:
        for i in rows:
            sold_delete.add(i)
            plan.removed_for_cancellation.append(sold[i][S["Title"]])
        return rows

    for ci, c in enumerate(cancels):
        info = cancel_info[ci]
        txn, items, ri = info["txn"], info["items"], info["row"]
        if not items:
            continue
        c_date = c.timestamp.strftime("%Y-%m-%d")
        detail = (c.cancellation_reason or "Order canceled").strip()
        if txn:
            detail += f" | Transaction {txn}"
        if len(items) > 1:
            detail += f" | Bundle: {', '.join(items)}"
        oid = (c.order_id or "").strip()
        display = items[0] if len(items) == 1 else f"Bundle {len(items)} items: {', '.join(items)}"

        if txn:
            moved = move_out(cancel_claims.get(txn, []))
        else:  # no shipping-label email: match by title among rows no order claimed
            moved = []
            if info["need"]:
                for i in sold_by_title.get(_norm(items[0]), []):
                    if i not in used_rows and i not in claimed and not str(sold[i][O]).strip():
                        used_rows.add(i)
                        moved = move_out([i])
                        break
        moved_urls = [str(sold[i][S["Vinted_URL"]]).strip() for i in moved if str(sold[i][S["Vinted_URL"]]).strip()]

        if ri is not None:
            r = cancelled[ri]
            changes = []
            if not str(r[C["Cancellation_Date"]]).strip():
                plan.cancelled_cell_updates.append((ri + 2, C["Cancellation_Date"], c_date))
                changes.append("date")
            if txn and txn not in str(r[C["Status_Details"]]):
                plan.cancelled_cell_updates.append((ri + 2, C["Status_Details"], detail))
                changes.append("details")
            if oid and not str(r[C["Order_ID"]]).strip():
                plan.cancelled_cell_updates.append((ri + 2, C["Order_ID"], oid))
            if moved_urls:
                kept = [u for u in str(r[C["Vinted_URL"]]).split("\n") if _ITEM_ID_RE.search(u)]
                plan.cancelled_cell_updates.append((ri + 2, C["Vinted_URL"], "\n".join(kept + moved_urls)))
                if len(items) == 1 and not str(r[C["Item_SKU"]]).strip():
                    plan.cancelled_cell_updates.append((ri + 2, C["Item_SKU"], sold[moved[0]][S["Item_SKU"]]))
                changes.append(f"moved {len(moved)} item(s) out of Sold_Items")
            if changes:
                plan.email_cancellations.append({"Title": display, "Date": c_date, "Action": "Updated existing record: " + ", ".join(changes)})
            continue
        if oid and oid in new_cancel_oids:
            continue

        first = sold[moved[0]] if moved else None
        crow = [""] * len(CANCELLED_ORDERS_HEADERS)
        crow[C["Order_ID"]] = oid or txn
        crow[C["Item_SKU"]] = first[S["Item_SKU"]] if (first and len(items) == 1) else ""
        crow[C["Item_Title"]] = display
        crow[C["Brand"]] = (first[S["Brand"]] if first else "") or (c.brand or "")
        crow[C["Category"]] = (first[S["Category"]] if first else "") or (c.category or "")
        crow[C["Lost_Revenue"]] = round(sum(parse_price(sold[i][S["Sold_Price"]]) for i in moved), 2) or c.sold_price
        crow[C["Original_Price"]] = round(sum(parse_price(sold[i][S["Posted_Price"]]) for i in moved), 2) or ""
        crow[C["Cancellation_Date"]] = c_date
        crow[C["Status_Details"]] = detail
        crow[C["Buyer_Username"]] = c.buyer_username or ""
        crow[C["Vinted_URL"]] = "\n".join(moved_urls) or (f"https://www.vinted.com/inbox/{oid}" if oid else "")
        crow[C["Source"]] = "Email"
        plan.cancelled_appends.append(crow)
        if oid:
            new_cancel_oids.add(oid)
        plan.email_cancellations.append({
            "Title": display, "Date": c_date,
            "Action": f"New cancellation: moved {len(moved)} item(s) out of Sold_Items" if moved else "New cancellation (item not in Sold_Items)",
        })

    # ---- 3. Offers -> Price_History -------------------------------------------
    new_listings = [list(r) for r in listings]
    for ev in evs:
        if ev.event_type != "offer" or not ev.offer_price:
            continue
        li = listing_by_vid.get(str(ev.vinted_id or "")) if ev.vinted_id else None
        if li is None:
            rows = listing_by_title.get(_norm(ev.item_title), [])
            li = rows[0] if len(rows) == 1 else None
        entry = f"{ev.timestamp.strftime('%m/%d')} Offer: ${ev.offer_price:.2f}"
        if ev.listed_price:
            entry += f" (was ${ev.listed_price:.2f})"
        if li is None or li in listing_moved:
            continue
        hist = str(new_listings[li][L["Price_History"]]).strip()
        if entry in hist:
            continue
        new_listings[li][L["Price_History"]] = f"{hist}; {entry}" if hist else entry
        plan.listings_changed = True
        plan.email_offers.append({"Title": new_listings[li][L["Title"]], "Offer": entry})

    if listing_moved:
        plan.listings_changed = True
    plan.final_listings = [r for i, r in enumerate(new_listings) if i not in listing_moved and not _blank(r)]

    # ---- Notes --------------------------------------------------------------------
    plan.sold_delete_rows = sorted((i + 2 for i in sold_delete), reverse=True)
    dated_rows = {r for r, c, _ in plan.sold_cell_updates if c == S["Sale_Date"]}
    still_undated = [
        r[S["Title"]] for i, r in enumerate(sold)
        if not _blank(r) and i not in sold_delete and not str(r[S["Sale_Date"]]).strip() and (i + 2) not in dated_rows
    ]
    if still_undated and labels:
        first_day = labels_by_date[0][1].timestamp.strftime("%Y-%m-%d")
        plan.warnings.append(
            f"{len(still_undated)} sold item(s) still have no sale date: no shipping-label email found for them "
            f"(emails scanned start {first_day}; widen the date range to include older sales)."
        )
    unresolved = [c.item_title for ci, c in enumerate(cancels) if ci not in cancel_txn]
    if unresolved:
        plan.warnings.append(
            "Cancellation emails without a matching shipping-label email were matched by title only: " + ", ".join(unresolved)
        )

    plan.counts_after = {
        "active": plan.counts_before["active"] - len(listing_moved),
        "sold": plan.counts_before["sold"] - len(sold_delete) + len(plan.sold_appends),
        "hidden": plan.counts_before["hidden"],
        "cancelled": plan.counts_before["cancelled"] + len(plan.cancelled_appends),
    }
    return plan


def build_manual_move_row(
    source_tab: str,
    dest_tab: str,
    source_row: List[Any],
    dest_sheet_row: int,
    item_data: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> List[Any]:
    """Builds a formatted row for dest_tab from source_row, mapping columns and applying row formulas."""
    now = now or datetime.now()
    data = item_data or {}

    sku = ""
    lot = ""
    title = ""
    brand = ""
    category = ""
    posted_price = 0.0
    current_price = 0.0
    sold_price = 0.0
    price_history = ""
    sale_date = ""
    platform = "Vinted"
    favorites = 0
    views = 0
    order_id = ""
    vinted_url = ""
    date_listed = ""

    if source_tab in (TAB_ITEM_LISTINGS, TAB_HIDDEN_ITEMS):
        srow = _pad(source_row, len(LISTINGS_HEADERS))
        sku = str(srow[L["Item_SKU"]])
        lot = str(srow[L["Lot_ID"]])
        title = str(srow[L["Title"]])
        brand = str(srow[L["Brand"]])
        category = str(srow[L["Category"]])
        posted_price = parse_price(srow[L["Posted_Price"]])
        current_price = parse_price(srow[L["Current_Price"]])
        sold_price = current_price or posted_price
        price_history = str(srow[L["Price_History"]])
        favorites = int(parse_price(srow[L["Favorites"]]))
        views = int(parse_price(srow[L["Views"]]))
        vinted_url = str(srow[L["Vinted_URL"]])
        date_listed = str(srow[L["Date_Listed"]])
    elif source_tab == TAB_SOLD_ITEMS:
        srow = _pad(source_row, len(SOLD_ITEMS_HEADERS))
        sku = str(srow[S["Item_SKU"]])
        lot = str(srow[S["Lot_ID"]])
        title = str(srow[S["Title"]])
        brand = str(srow[S["Brand"]])
        category = str(srow[S["Category"]])
        posted_price = parse_price(srow[S["Posted_Price"]])
        current_price = parse_price(srow[S["Current_Price"]])
        sold_price = parse_price(srow[S["Sold_Price"]]) or current_price or posted_price
        price_history = str(srow[S["Price_History"]])
        sale_date = str(srow[S["Sale_Date"]])
        platform = str(srow[S["Platform"]]) or "Vinted"
        favorites = int(parse_price(srow[S["Favorites"]]))
        views = int(parse_price(srow[S["Views"]]))
        order_id = str(srow[S["Order_ID"]])
        vinted_url = str(srow[S["Vinted_URL"]])
        date_listed = str(srow[S["Date_Listed"]])
    elif source_tab == TAB_CANCELLED_ORDERS:
        srow = _pad(source_row, len(CANCELLED_ORDERS_HEADERS))
        order_id = str(srow[C["Order_ID"]])
        sku = str(srow[C["Item_SKU"]])
        title = str(srow[C["Item_Title"]])
        brand = str(srow[C["Brand"]])
        category = str(srow[C["Category"]])
        sold_price = parse_price(srow[C["Lost_Revenue"]])
        current_price = parse_price(srow[C["Original_Price"]])
        posted_price = current_price
        vinted_url = str(srow[C["Vinted_URL"]])

    # Apply overrides
    if "sku" in data and data["sku"]:
        sku = str(data["sku"])
    if "lot" in data:
        lot = str(data["lot"])
    if "title" in data and data["title"]:
        title = str(data["title"])
    if "brand" in data and data["brand"]:
        brand = str(data["brand"])
    if "category" in data and data["category"]:
        category = str(data["category"])
    if "posted_price" in data and data["posted_price"] is not None:
        posted_price = parse_price(data["posted_price"])
    if "current_price" in data and data["current_price"] is not None:
        current_price = parse_price(data["current_price"])
    if "sold_price" in data and data["sold_price"] is not None:
        sold_price = parse_price(data["sold_price"])
    if "sale_date" in data and data["sale_date"]:
        sale_date = str(data["sale_date"])
    if "platform" in data and data["platform"]:
        platform = str(data["platform"])
    if "order_id" in data:
        order_id = str(data["order_id"])

    # Build destination row
    if dest_tab == TAB_SOLD_ITEMS:
        drow = [""] * len(SOLD_ITEMS_HEADERS)
        drow[S["Item_SKU"]] = sku
        drow[S["Lot_ID"]] = lot
        drow[S["Title"]] = title
        drow[S["Brand"]] = brand
        drow[S["Category"]] = category
        drow[S["Posted_Price"]] = posted_price or current_price or sold_price
        drow[S["Current_Price"]] = current_price or posted_price or sold_price
        drow[S["Price_History"]] = price_history
        drow[S["Sold_Price"]] = sold_price
        drow[S["Sale_Date"]] = sale_date or now.strftime("%Y-%m-%d")
        drow[S["Platform"]] = platform
        drow[S["Favorites"]] = favorites
        drow[S["Views"]] = views
        drow[S["Order_ID"]] = order_id
        drow[S["Vinted_URL"]] = vinted_url
        drow[S["Status"]] = data.get("status") or "Sold"
        drow[S["Date_Listed"]] = date_listed or now.strftime("%Y-%m-%d")
        drow[S["Source"]] = "Manual"
        return with_formulas(drow, dest_sheet_row, sold_formulas)

    if dest_tab in (TAB_ITEM_LISTINGS, TAB_HIDDEN_ITEMS):
        drow = [""] * len(LISTINGS_HEADERS)
        drow[L["Item_SKU"]] = sku
        drow[L["Lot_ID"]] = lot
        drow[L["Title"]] = title
        drow[L["Brand"]] = brand
        drow[L["Category"]] = category
        drow[L["Posted_Price"]] = posted_price or current_price or sold_price
        drow[L["Current_Price"]] = current_price or posted_price or sold_price
        drow[L["Price_History"]] = price_history
        drow[L["Favorites"]] = favorites
        drow[L["Views"]] = views
        drow[L["Vinted_ID"]] = _vid_from_url(vinted_url)
        drow[L["Vinted_URL"]] = vinted_url
        drow[L["Status"]] = data.get("status") or ("Hidden" if dest_tab == TAB_HIDDEN_ITEMS else "Listed")
        drow[L["Date_Listed"]] = date_listed or now.strftime("%Y-%m-%d")
        drow[L["Last_Synced"]] = now.strftime("%Y-%m-%d %H:%M:%S")
        drow[L["Source"]] = "Manual"
        return with_formulas(drow, dest_sheet_row, listing_formulas)

    if dest_tab == TAB_CANCELLED_ORDERS:
        drow = [""] * len(CANCELLED_ORDERS_HEADERS)
        drow[C["Order_ID"]] = order_id or (f"CAN-{sku}" if sku else f"CAN-{now.strftime('%Y%m%d%H%M')}")
        drow[C["Item_SKU"]] = sku
        drow[C["Item_Title"]] = title
        drow[C["Brand"]] = brand
        drow[C["Category"]] = category
        drow[C["Lost_Revenue"]] = sold_price or current_price or posted_price
        drow[C["Original_Price"]] = current_price or posted_price or sold_price
        drow[C["Cancellation_Date"]] = data.get("cancellation_date") or sale_date or now.strftime("%Y-%m-%d")
        drow[C["Status_Details"]] = data.get("notes") or data.get("status_details") or "Manually marked cancelled"
        drow[C["Buyer_Username"]] = data.get("buyer_username", "")
        drow[C["Vinted_URL"]] = vinted_url
        drow[C["Source"]] = "Manual"
        return drow

    raise ValueError(f"Unsupported destination tab: {dest_tab}")

