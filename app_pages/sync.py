"""Sync & Batch Import page for Vinted sales and inventory tracking."""

import os
import time
import html
from datetime import datetime, date, timedelta
from typing import Tuple, Optional
import streamlit as st
import pandas as pd

from src.config import settings
from src.sheets import GoogleSheetsClient, SheetsSyncEngine
from src.sheets.sync import parse_price
from src.html_parser import parse_closet_html, parse_orders_html, detect_html_type
from src.email_parser import fetch_vinted_emails_via_imap
from src.notifications import record_html_parse_event, record_email_parse_event, get_sync_status
from src.telemetry import (
    log_event,
    log_error,
    get_user_email_credentials,
    save_user_email_credentials,
    clear_user_email_credentials,
    save_email_credentials_to_env,
)


def get_sheets_client() -> GoogleSheetsClient:
    """Returns a GoogleSheetsClient configured for the current session user."""
    return GoogleSheetsClient(
        sheet_id=st.session_state.get("spreadsheet_id", settings.google_sheet_id),
        credentials=st.session_state.get("user_credentials"),
    )


def get_sheets_sync_engine(client: GoogleSheetsClient) -> SheetsSyncEngine:
    """Returns a SheetsSyncEngine instance, ensuring module freshness if hot-reloaded by Streamlit Cloud."""
    import importlib
    import src.sheets.sync
    import src.sheets
    from src.sheets.sync import SheetsSyncEngine as EngineClass

    # Check if the class definition currently in sys.modules is stale
    if not hasattr(EngineClass, "reconcile_missing_sold_metadata") or not hasattr(EngineClass, "clean_blank_rows"):
        try:
            importlib.reload(src.sheets.sync)
            importlib.reload(src.sheets)
            from src.sheets.sync import SheetsSyncEngine as ReloadedClass
            return ReloadedClass(client)
        except Exception:
            pass
    return EngineClass(client)


@st.cache_data(ttl=60)
def get_cached_counts(sheet_id: str, _creds):
    """Fetches active and sold counts with batch get (1 API call) and 60-second cache."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    sh = client.get_spreadsheet()
    try:
        res = sh.values_batch_get(["Item_Listings!A:A", "Sold_Items!A:A"])
        val_ranges = res.get("valueRanges", [])
        active_rows = val_ranges[0].get("values", []) if len(val_ranges) > 0 else []
        sold_rows = val_ranges[1].get("values", []) if len(val_ranges) > 1 else []
        active_cnt = sum(1 for r in active_rows[1:] if any(str(c).strip() for c in r))
        sold_cnt = sum(1 for r in sold_rows[1:] if any(str(c).strip() for c in r))
        return active_cnt, sold_cnt
    except Exception:
        ws_l = sh.worksheet("Item_Listings")
        ws_s = sh.worksheet("Sold_Items")
        active_cnt = sum(1 for r in ws_l.get_all_values()[1:] if any(str(c).strip() for c in r))
        sold_cnt = sum(1 for r in ws_s.get_all_values()[1:] if any(str(c).strip() for c in r))
        return active_cnt, sold_cnt


@st.cache_data(ttl=60)
def get_cached_lots(sheet_id: str, _creds):
    """Fetches all lots with 60-second cache."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    engine = get_sheets_sync_engine(client)
    return engine.get_all_lots()


@st.cache_data(ttl=60)
def get_cached_supplies(sheet_id: str, _creds):
    """Fetches all shipping supplies with 60-second cache."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    engine = get_sheets_sync_engine(client)
    return engine.get_all_supplies()


@st.cache_data(ttl=60)
def get_cached_titles(sheet_id: str, _creds):
    """Batch-fetches Title column from Item_Listings and Sold_Items in 1 single call."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    sh = client.get_spreadsheet()
    try:
        res = sh.values_batch_get(["Item_Listings!C:C", "Sold_Items!C:C"])
        val_ranges = res.get("valueRanges", [])
        active_titles = {str(r[0]).strip().lower() for r in val_ranges[0].get("values", [])[1:] if r and len(r) > 0} if len(val_ranges) > 0 else set()
        sold_titles = {str(r[0]).strip().lower() for r in val_ranges[1].get("values", [])[1:] if r and len(r) > 0} if len(val_ranges) > 1 else set()
        return active_titles, sold_titles
    except Exception:
        ws_list = sh.worksheet("Item_Listings")
        ws_sold = sh.worksheet("Sold_Items")
        active_titles = {str(r[2]).strip().lower() for r in ws_list.get_all_values()[1:] if len(r) > 2}
        sold_titles = {str(r[2]).strip().lower() for r in ws_sold.get_all_values()[1:] if len(r) > 2}
        return active_titles, sold_titles


def format_sync_timestamp(raw_ts: Optional[str]) -> Tuple[str, str]:
    """Formats a raw sync timestamp into a clean, legible string and relative time."""
    if not raw_ts or str(raw_ts).strip().lower() in ("not yet run", "none", ""):
        return ("Not yet run", "Awaiting sync")
    ts_clean = str(raw_ts).strip()
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts_clean[:19], fmt)
            break
        except Exception:
            continue
    if not dt:
        return (ts_clean, "Recorded")

    now = datetime.now()
    diff = now - dt
    tot_secs = int(diff.total_seconds())
    if tot_secs < 0:
        rel = "Just now"
    elif tot_secs < 60:
        rel = "Just now"
    elif tot_secs < 3600:
        rel = f"{tot_secs // 60}m ago"
    elif tot_secs < 86400:
        rel = f"{tot_secs // 3600}h ago"
    elif tot_secs < 86400 * 7:
        rel = f"{tot_secs // 86400}d ago"
    else:
        rel = dt.strftime("%b %d")

    # Safe cross-platform 12-hour format without %-I / %#I
    hour_12 = dt.hour % 12 or 12
    minute = f"{dt.minute:02d}"
    am_pm = "AM" if dt.hour < 12 else "PM"
    time_str = f"{hour_12}:{minute} {am_pm}"

    if dt.date() == now.date():
        display = f"Today, {time_str}"
    elif dt.date() == (now - timedelta(days=1)).date():
        display = f"Yesterday, {time_str}"
    else:
        month_str = dt.strftime("%b")
        display = f"{month_str} {dt.day}, {time_str}"

    return (display, rel)


def render_sync_kpi_cards(
    active_cnt: int,
    sold_cnt: int,
    raw_h_time: Optional[str],
    raw_e_time: Optional[str],
    h_details: Optional[str] = "",
    e_details: Optional[str] = "",
):
    """Renders modern, responsive KPI cards with clean typography and custom color options."""
    h_display, h_rel = format_sync_timestamp(raw_h_time)
    e_display, e_rel = format_sync_timestamp(raw_e_time)

    # Style state initialization - Default to Dark
    if "sync_metric_style" not in st.session_state or st.session_state.get("sync_metric_style") == "Vibrant Tinted":
        st.session_state["sync_metric_style"] = "Deep Slate (Dark)"
    if "sync_card_custom_bg" not in st.session_state:
        st.session_state["sync_card_custom_bg"] = "#0f172a"
    if "sync_card_custom_text" not in st.session_state:
        st.session_state["sync_card_custom_text"] = "#f8fafc"
    if "sync_card_custom_border" not in st.session_state:
        st.session_state["sync_card_custom_border"] = "#3b82f6"

    # Style control bar
    c_head, c_btn = st.columns([5, 1], vertical_alignment="center")
    with c_head:
        st.markdown("##### 📊 Sync Status & Metrics")
    with c_btn:
        with st.popover("🎨 Card Style", help="Customize card background, text color, and themes"):
            st.markdown("**Metric Card Styling**")
            theme_choice = st.radio(
                "Theme Preset",
                options=["Deep Slate (Dark)", "Vibrant Dark", "Minimalist Dark", "Custom Colors", "Native Streamlit"],
                index=["Deep Slate (Dark)", "Vibrant Dark", "Minimalist Dark", "Custom Colors", "Native Streamlit"].index(
                    st.session_state.get("sync_metric_style", "Deep Slate (Dark)")
                    if st.session_state.get("sync_metric_style") in ["Deep Slate (Dark)", "Vibrant Dark", "Minimalist Dark", "Custom Colors", "Native Streamlit"]
                    else "Deep Slate (Dark)"
                ),
                key="sync_metric_style_picker",
            )
            st.session_state["sync_metric_style"] = theme_choice

            if theme_choice == "Custom Colors":
                st.markdown("---")
                st.caption("Customize card colors:")
                st.session_state["sync_card_custom_bg"] = st.color_picker(
                    "Background Color",
                    value=st.session_state.get("sync_card_custom_bg", "#0f172a"),
                    key="picker_custom_bg",
                )
                st.session_state["sync_card_custom_text"] = st.color_picker(
                    "Text Color",
                    value=st.session_state.get("sync_card_custom_text", "#f8fafc"),
                    key="picker_custom_text",
                )
                st.session_state["sync_card_custom_border"] = st.color_picker(
                    "Border / Accent Color",
                    value=st.session_state.get("sync_card_custom_border", "#3b82f6"),
                    key="picker_custom_border",
                )

    style_mode = st.session_state.get("sync_metric_style", "Deep Slate (Dark)")

    # Native Streamlit fallback
    if style_mode == "Native Streamlit":
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Active Listings", f"{active_cnt:,}", border=True)
        with m2:
            st.metric("Sold Items", f"{sold_cnt:,}", border=True)
        with m3:
            st.metric("Last HTML Import", h_display, delta=h_rel if h_rel != "Awaiting sync" else None, delta_color="off", border=True)
        with m4:
            st.metric("Last Email Parsed", e_display, delta=e_rel if e_rel != "Awaiting sync" else None, delta_color="off", border=True)
        return

    # Subtitles
    h_sub = f"{h_rel} • HTML Upload" if h_rel != "Awaiting sync" else "Awaiting import"
    e_sub = f"{e_rel} • Gmail IMAP" if e_rel != "Awaiting sync" else "Awaiting sync"

    # Palette configurations (Defaulting to Dark backgrounds)
    if style_mode == "Minimalist Dark":
        cards = [
            {"label": "Active Listings", "icon": "🏷️", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#161b22", "border": "#30363d", "accent": "#58a6ff", "text": "#f0f6fc"},
            {"label": "Sold Items", "icon": "💰", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#161b22", "border": "#30363d", "accent": "#3fb950", "text": "#f0f6fc"},
            {"label": "Last HTML Import", "icon": "🌐", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#161b22", "border": "#30363d", "accent": "#d29922", "text": "#f0f6fc"},
            {"label": "Last Email Parsed", "icon": "✉️", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#161b22", "border": "#30363d", "accent": "#bc8cff", "text": "#f0f6fc"},
        ]
    elif style_mode == "Vibrant Dark":
        cards = [
            {"label": "Active Listings", "icon": "🏷️", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#0c1e3d", "border": "#2563eb", "accent": "#60a5fa", "text": "#f8fafc"},
            {"label": "Sold Items", "icon": "💰", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#062e24", "border": "#059669", "accent": "#34d399", "text": "#f8fafc"},
            {"label": "Last HTML Import", "icon": "🌐", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#361e05", "border": "#d97706", "accent": "#fbbf24", "text": "#f8fafc"},
            {"label": "Last Email Parsed", "icon": "✉️", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#260e42", "border": "#7c3aed", "accent": "#c084fc", "text": "#f8fafc"},
        ]
    elif style_mode == "Custom Colors":
        c_bg = st.session_state.get("sync_card_custom_bg", "#0f172a")
        c_text = st.session_state.get("sync_card_custom_text", "#f8fafc")
        c_border = st.session_state.get("sync_card_custom_border", "#3b82f6")
        cards = [
            {"label": "Active Listings", "icon": "🏷️", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Sold Items", "icon": "💰", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Last HTML Import", "icon": "🌐", "value": h_display, "sub": h_sub, "is_date": True, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Last Email Parsed", "icon": "✉️", "value": e_display, "sub": e_sub, "is_date": True, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
        ]
    else:  # "Deep Slate (Dark)" (Default)
        cards = [
            {"label": "Active Listings", "icon": "🏷️", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#0f172a", "border": "#1e3a8a", "accent": "#60a5fa", "text": "#f8fafc"},
            {"label": "Sold Items", "icon": "💰", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#0f172a", "border": "#065f46", "accent": "#34d399", "text": "#f8fafc"},
            {"label": "Last HTML Import", "icon": "🌐", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#0f172a", "border": "#854d0e", "accent": "#fbbf24", "text": "#f8fafc"},
            {"label": "Last Email Parsed", "icon": "✉️", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#0f172a", "border": "#581c87", "accent": "#c084fc", "text": "#f8fafc"},
        ]

    # Generate CSS & Grid HTML
    cards_html_parts = []
    for c in cards:
        val_class = "sync-metric-val-date" if c["is_date"] else "sync-metric-val-num"
        card_html = f'''
        <div class="sync-metric-card" style="background: {c['bg']}; border-color: {c['border']}; color: {c['text']};">
          <div class="sync-metric-header">
            <span class="sync-metric-label" style="color: {c['accent']};">{html.escape(c['label'])}</span>
            <span class="sync-metric-icon">{c['icon']}</span>
          </div>
          <div class="{val_class}" title="{html.escape(c['value'])}">{html.escape(c['value'])}</div>
          <div class="sync-metric-footer">
            <span class="sync-metric-badge" style="border-color: {c['border']};">{html.escape(c['sub'])}</span>
          </div>
        </div>'''
        cards_html_parts.append(card_html)

    html_content = f'''
    <style>
    .sync-metrics-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 12px;
        margin-top: 4px;
        margin-bottom: 1.25rem;
    }}
    .sync-metric-card {{
        border-radius: 12px;
        padding: 14px 16px;
        border: 1px solid;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 104px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .sync-metric-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    }}
    .sync-metric-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 6px;
    }}
    .sync-metric-label {{
        font-size: 0.74rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}
    .sync-metric-icon {{
        font-size: 1.05rem;
        line-height: 1;
    }}
    .sync-metric-val-num {{
        font-size: 1.85rem;
        font-weight: 700;
        line-height: 1.15;
        margin-bottom: 6px;
        letter-spacing: -0.02em;
    }}
    .sync-metric-val-date {{
        font-size: 1.10rem;
        font-weight: 600;
        line-height: 1.25;
        margin-bottom: 6px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .sync-metric-footer {{
        display: flex;
        align-items: center;
        margin-top: auto;
    }}
    .sync-metric-badge {{
        display: inline-block;
        padding: 2px 7px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 500;
        opacity: 0.85;
        background: rgba(128, 128, 128, 0.1);
        border: 1px solid transparent;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    </style>
    <div class="sync-metrics-grid">
      {''.join(cards_html_parts)}
    </div>
    '''
    st.html(html_content)


def render_sync_page():
    st.header("⚡ Thread Count Operations & Sync Hub")
    st.caption("From inbox alerts to bottom-line gains. Reconcile inventory via HTML uploads, live Gmail sync, or supply tracking.")

    client = get_sheets_client()
    status_state = get_sync_status()
    u_email = (st.session_state.get("user_info", {}).get("email") or "anonymous").lower()
    sheet_id = st.session_state.get("spreadsheet_id", settings.google_sheet_id)
    user_creds = st.session_state.get("user_credentials")

    try:
        active_cnt, sold_cnt = get_cached_counts(sheet_id, user_creds)
        connected = True
    except Exception as e:
        if "429" in str(e):
            st.warning("⏳ Google Sheets rate limit reached (60 read requests/min). Please wait 30 seconds for Google's quota to reset.")
            st.button("🔄 Retry Connection", on_click=lambda: st.cache_data.clear(), key="retry_rate_limit_btn")
            return
        active_cnt = 0
        sold_cnt = 0
        connected = False
        log_error(u_email, "sync.py:connect", e)

    # KPI Metric Row
    render_sync_kpi_cards(
        active_cnt=active_cnt,
        sold_cnt=sold_cnt,
        raw_h_time=status_state.get("last_html_parsed_at"),
        raw_e_time=status_state.get("last_email_parsed_at"),
        h_details=status_state.get("last_html_details", ""),
        e_details=status_state.get("last_email_details", ""),
    )

    if not connected:
        st.warning("⚠️ Could not connect to Google Spreadsheet. Check permissions or ensure your spreadsheet is shared with your account.")
        return

    # Quick Maintenance Tools
    with st.expander("🛠️ Spreadsheet Tools", expanded=False):
        c_tool1, c_tool2, c_tool3 = st.columns(3)
        with c_tool1:
            if st.button("🧹 Clean Blank Rows", type="secondary", width="stretch", icon=":material/cleaning_services:", key="clean_blank_rows_btn"):
                with st.spinner("Scanning and purging blank rows from Google Sheets..."):
                    engine = get_sheets_sync_engine(client)
                    if not hasattr(engine, "clean_blank_rows"):
                        import importlib
                        import src.sheets.sync
                        importlib.reload(src.sheets.sync)
                        from src.sheets.sync import SheetsSyncEngine as FallbackEngine
                        engine = FallbackEngine(client)
                    res = engine.clean_blank_rows()
                    st.cache_data.clear()
                    total_cleaned = sum(res.values())
                    if total_cleaned > 0:
                        details = ", ".join(f"{tab}: {cnt}" for tab, cnt in res.items() if cnt > 0)
                        st.success(f"✔ Purged {total_cleaned} blank row(s) from spreadsheet ({details})!")
                    else:
                        st.info("✔ No blank rows detected in your spreadsheet. All clean!")
                    time.sleep(1.2)
                    st.rerun()
        with c_tool2:
            saved_u, saved_p = get_user_email_credentials(u_email)
            active_email_user = saved_u or os.getenv("GMAIL_USER", "") or st.session_state.get("email_sync_user", "")
            active_email_pass = saved_p or os.getenv("GMAIL_APP_PASSWORD", "") or st.session_state.get("gmail_pass_input", "")

            if st.button("✨ Backfill Missing Metadata in Sold", type="secondary", width="stretch", icon=":material/auto_fix_high:", key="backfill_metadata_btn"):
                spinner_text = "Analyzing Sold_Items and restoring missing Titles, Lots, Brands, and Categories"
                if active_email_user and active_email_pass:
                    spinner_text += f" (re-scanning Vinted-Processed emails from {active_email_user})..."
                else:
                    spinner_text += "..."
                with st.spinner(spinner_text):
                    engine = get_sheets_sync_engine(client)
                    if not hasattr(engine, "reconcile_missing_sold_metadata"):
                        import importlib
                        import src.sheets.sync
                        importlib.reload(src.sheets.sync)
                        from src.sheets.sync import SheetsSyncEngine as FallbackEngine
                        engine = FallbackEngine(client)
                    res = engine.reconcile_missing_sold_metadata(
                        default_lot=st.session_state.get("selected_lot", ""),
                        gmail_user=active_email_user,
                        gmail_password=active_email_pass,
                        max_emails_scan=250,
                    )
                    st.cache_data.clear()
                    if res["fixed_rows"] > 0:
                        email_info = f" from {res.get('emails_scanned', 0)} scanned emails" if res.get('emails_scanned', 0) > 0 else ""
                        st.success(
                            f"✔ Enriched {res['fixed_rows']} sold items{email_info}! "
                            f"({res['titles_fixed']} titles restored, {res['lots_assigned']} lots assigned, "
                            f"{res['brands_fixed']} brands, {res['categories_fixed']} categories)"
                        )
                    else:
                        email_scanned_msg = f" (Scanned {res.get('emails_scanned', 0)} emails)" if res.get('emails_scanned', 0) > 0 else ""
                        st.info(f"✔ All items in Sold_Items already have full metadata!{email_scanned_msg}")
                    time.sleep(1.5)
                    st.rerun()

            if active_email_user:
                st.caption(f"📬 Gmail: `{active_email_user}` (re-scans `Vinted-Processed` emails)")
            else:
                st.caption("💡 Enter Gmail in **Direct Email Sync** tab to re-scan `Vinted-Processed` emails.")
        with c_tool3:
            if st.button("🔄 Force Refresh Data", type="secondary", width="stretch", icon=":material/refresh:", key="force_refresh_data_btn"):
                st.cache_data.clear()
                st.success("✔ Cache cleared! Reloading fresh data...")
                time.sleep(0.5)
                st.rerun()

    # -------------------------------------------------------------------------
    # Main Tabs: HTML Import & Email Sync
    # -------------------------------------------------------------------------
    tab_html, tab_email, tab_supplies = st.tabs([
        "📦 HTML Batch Import & Reconciliation",
        "📬 Direct Email Sync",
        "🏷️ Shipping Supplies",
    ])

    # ---------------------------------------------------------
    # TAB 1: HTML IMPORT
    # ---------------------------------------------------------
    with tab_html:
        engine = get_sheets_sync_engine(client)
        try:
            all_lots = get_cached_lots(sheet_id, user_creds)
        except Exception as e:
            if "429" in str(e):
                st.warning("⏳ Google Sheets quota reached. Waiting for reset...")
                st.button("🔄 Retry Lots", on_click=lambda: st.cache_data.clear(), key="retry_lots_btn")
            all_lots = []

        # -----------------------------------------------------------------
        # 1. Inventory Lot Assignment (Always Visible)
        # -----------------------------------------------------------------
        with st.container(border=True):
            st.markdown("### 🏷️ Inventory Lot Assignment")
            st.caption("Choose which inventory lot new items should be assigned to, leave unassigned, or register a new lot.")

            NO_LOT_OPTION = "⚪ None / No Lot (Unassigned)"
            lot_options = [NO_LOT_OPTION]
            lot_id_map = {NO_LOT_OPTION: ""}

            for l in all_lots:
                lid = l.get("Lot_ID", "")
                if not lid:
                    continue
                vendor = l.get("Source_Vendor", "Vendor")
                cost = l.get("Unit_Cost", "$0.00")
                label = f"{lid} — {vendor} ({cost}/ea)"
                lot_options.append(label)
                lot_id_map[label] = lid

            lot_options.append("➕ Add New Lot...")

            # Determine default index based on current session lot
            default_idx = 0
            cur_selected = st.session_state.get("selected_lot", "")
            if cur_selected:
                for idx, opt in enumerate(lot_options):
                    if opt != NO_LOT_OPTION and opt.startswith(cur_selected):
                        default_idx = idx
                        break
            else:
                default_idx = 0

            selected_option = st.selectbox(
                "Select Inventory Lot for New Listings:",
                options=lot_options,
                index=default_idx,
                help="Select an existing lot from your sheet, choose '⚪ None / No Lot' to leave unassigned, or '➕ Add New Lot...' to register a new haul.",
                key="sync_lot_selectbox",
            )

            is_new_lot = (selected_option == "➕ Add New Lot...")
            is_none_lot = (selected_option == NO_LOT_OPTION)

            if is_new_lot:
                with st.container(border=True):
                    st.markdown("#### ✨ Define New Inventory Lot")
                    st.caption("Fill out the lot details below. You can save it immediately or have it automatically created when syncing HTML files.")
                    next_suggested_id = engine.get_next_lot_id(cached_lots=all_lots)

                    nl_c1, nl_c2, nl_c3 = st.columns(3)
                    with nl_c1:
                        new_lot_id = st.text_input("Lot ID", value=next_suggested_id, help="e.g. LOT-002", key="input_new_lot_id")
                        new_purchase_date = st.date_input("Purchase Date", value=date.today(), key="input_new_lot_date")
                    with nl_c2:
                        new_vendor = st.text_input("Source Vendor", placeholder="e.g. Goodwill, Plato's Closet, Wholesale Lot", key="input_new_lot_vendor")
                        new_status = st.selectbox("Status", options=["Active", "Pending", "Completed"], index=0, key="input_new_lot_status")
                    with nl_c3:
                        new_total_cost = st.number_input("Total Lot Cost ($)", min_value=0.0, value=50.0, step=5.0, key="input_new_lot_cost")
                        new_total_articles = st.number_input("Total Articles in Lot", min_value=1, value=25, step=1, key="input_new_lot_articles")

                    calc_unit_cost = (new_total_cost / new_total_articles) if new_total_articles > 0 else 0.0
                    st.info(f"💡 Calculated Unit Cost per item: **${calc_unit_cost:.2f}** (`${new_total_cost:.2f} ÷ {new_total_articles} articles`)")

                    new_notes = st.text_input("Notes", placeholder="e.g. 50 assorted fall sweaters and flannels", key="input_new_lot_notes")
                    target_lot_id = new_lot_id.strip().upper() if new_lot_id else next_suggested_id

                    def on_cancel_new_lot():
                        st.session_state["sync_lot_selectbox"] = NO_LOT_OPTION

                    btn_c1, btn_c2 = st.columns([2, 1], vertical_alignment="bottom")
                    with btn_c1:
                        if st.button("💾 Save New Lot to Spreadsheet Now", type="secondary", width="stretch"):
                            with st.spinner(f"Saving {target_lot_id} to Google Sheets..."):
                                res = engine.add_inventory_lot(
                                    lot_id=target_lot_id,
                                    purchase_date=new_purchase_date.strftime("%Y-%m-%d"),
                                    source_vendor=new_vendor,
                                    total_cost=float(new_total_cost),
                                    total_articles=int(new_total_articles),
                                    status=new_status,
                                    notes=new_notes,
                                )
                                engine.sync_lots()
                                log_event(u_email, "lot_created", f"Created lot {target_lot_id} ({new_total_articles} articles)", spreadsheet_id=st.session_state.get("spreadsheet_id"))
                                st.session_state.selected_lot = target_lot_id
                                st.cache_data.clear()
                            if res.get("status") == "exists":
                                st.warning(f"Lot {target_lot_id} already exists in your spreadsheet.")
                            else:
                                st.success(f"✔ Lot {target_lot_id} successfully saved to Inventory_Lots!")
                            time.sleep(1.2)
                            st.rerun()
                    with btn_c2:
                        st.button("Cancel", on_click=on_cancel_new_lot, type="tertiary", icon=":material/close:", width="stretch")
            elif is_none_lot:
                target_lot_id = ""
                st.session_state.selected_lot = ""
                st.info("ℹ️ **No Lot Selected**: New items will be imported without an assigned inventory lot (Unit Cost = $0.00). You can choose lots individually in the preview editor below or assign them later in Google Sheets.")
            else:
                target_lot_id = lot_id_map.get(selected_option, "")
                st.session_state.selected_lot = target_lot_id
                selected_lot_info = next((l for l in all_lots if l.get("Lot_ID") == target_lot_id), None)
                if selected_lot_info:
                    c_info1, c_info2, c_info3, c_info4 = st.columns(4)
                    c_info1.caption(f"**Vendor:** {selected_lot_info.get('Source_Vendor', 'N/A')}")
                    c_info2.caption(f"**Unit Cost:** {selected_lot_info.get('Unit_Cost', 'N/A')}")
                    c_info3.caption(f"**Processed:** {selected_lot_info.get('Items_Processed', '0')} / {selected_lot_info.get('Total_Articles', '0')} items")
                    c_info4.caption(f"**Status:** {selected_lot_info.get('Status', 'Active')}")

        st.divider()

        # -----------------------------------------------------------------
        # 2. HTML Batch File Uploader
        # -----------------------------------------------------------------
        lot_display_str = f"**{target_lot_id}**" if target_lot_id else "*No Lot (Unassigned)*"
        st.markdown("### Upload Saved Vinted HTML Files")
        st.caption(f"Drag and drop your saved HTML files (`Active Listings`, `Sold Listings`, `My Orders`). Newly detected items will default to {lot_display_str}.")

        with st.expander("ℹ️ How to export your FULL catalog (180+ items) from Vinted", expanded=False):
            st.markdown(
                """
                **Why does an uploaded file only show 20 items?**
                Vinted uses **infinite scrolling** (lazy loading). When you first load your profile or orders page, your browser only renders the first **20 items**. As you scroll down, Vinted loads more items into the page.
                
                If you save the page (`Cmd+S` or `Ctrl+S`) before scrolling down, your browser only saves the items currently visible in memory!
                
                **How to save all items (e.g. 183 active & 166 sold):**
                1. Open your Vinted closet (`vinted.com/member/...`) or Orders page in Chrome, Safari, or Edge.
                2. **Scroll all the way down to the bottom** until all your items have loaded (you can hold down the **Page Down** or **End** key).
                3. Press **Cmd + S** (Mac) or **Ctrl + S** (Windows) $\\rightarrow$ select **"Webpage, Complete"** or **"Webpage, HTML Only"**.
                4. Upload the newly saved `.html` file here.
                
                *⚡ Pro Tip: 5-Second Auto-Scroll Console Snippet:*
                If you have a large closet and don't want to scroll manually, open Developer Tools Console (`Cmd+Option+J` on Mac or `Ctrl+Shift+J` on Windows / Chrome), paste this snippet, and hit Enter:
                ```javascript
                let t = setInterval(() => { window.scrollBy(0, 1500); if (window.innerHeight + window.scrollY >= document.body.offsetHeight) { clearInterval(t); alert("All items loaded! Press Cmd+S to save."); } }, 250);
                ```
                """
            )

        uploaded_files = st.file_uploader(
            "Drop Vinted HTML files here",
            type=["html", "htm"],
            accept_multiple_files=True,
            help="Upload the .html files saved via your browser (Cmd+S / Ctrl+S)",
        )

        if uploaded_files:
            st.markdown("#### 🔍 Detected Files & Content Analysis")
            files_info = []
            parsed_orders = []
            parsed_closet = []

            for f in uploaded_files:
                file_bytes = f.getvalue()
                detected = detect_html_type(file_bytes)
                
                if detected == "orders":
                    orders = parse_orders_html(file_bytes)
                    parsed_orders.extend(orders)
                    files_info.append({"Filename": f.name, "Detected Type": "💰 My Orders", "Items Found": len(orders)})
                elif detected in ("active_closet", "sold_closet"):
                    items = parse_closet_html(file_bytes, default_lot=target_lot_id)
                    parsed_closet.extend(items)
                    type_label = "📦 Active Listings" if detected == "active_closet" else "🏷️ Sold Closet"
                    files_info.append({"Filename": f.name, "Detected Type": type_label, "Items Found": len(items)})
                else:
                    files_info.append({"Filename": f.name, "Detected Type": "❓ Unknown", "Items Found": 0})

            st.dataframe(pd.DataFrame(files_info), width="stretch")

            # -------------------------------------------------------------
            # 3. Reconciliation Preview (Zero-Duplication & Interactive Editing)
            # -------------------------------------------------------------
            if parsed_orders or parsed_closet:
                st.divider()
                lot_preview_header = f" (Default Lot: {target_lot_id})" if target_lot_id else " (Default Lot: None)"
                st.markdown(f"#### ⚡ Reconciliation Preview{lot_preview_header}")
                
                try:
                    curr_active_titles, curr_sold_titles = get_cached_titles(sheet_id, user_creds)

                    new_listings = [it for it in parsed_closet if it.status != "Sold" and it.title.strip().lower() not in curr_active_titles and it.title.strip().lower() not in curr_sold_titles]
                    sold_transitions = [it for it in parsed_orders if it.item_title.strip().lower() in curr_active_titles]
                    already_sold = [it for it in parsed_orders if it.item_title.strip().lower() in curr_sold_titles]

                    # Assign target lot ID to new items in preview if not already populated
                    for it in new_listings:
                        if not it.lot_id and target_lot_id:
                            it.lot_id = target_lot_id

                    p1, p2, p3 = st.columns(3)
                    p1.metric("🟢 New Active to Add", len(new_listings), f"Default Lot: {target_lot_id or 'None'}", border=True)
                    p2.metric("🔵 Active Moving to Sold", len(sold_transitions), border=True)
                    p3.metric("⚪ Duplicates Skipped", len(already_sold) + (len(parsed_closet) - len(new_listings)), border=True)

                    if sold_transitions:
                        with st.expander("View Items Moving to Sold", expanded=False):
                            st.dataframe(pd.DataFrame([{"Title": o.item_title, "Price": f"${o.sold_price:.2f}", "Order ID": o.order_id} for o in sold_transitions]), width="stretch")

                    edited_listings_df = None
                    if new_listings:
                        with st.expander(f"View New Listings to Add ({len(new_listings)} items — Editable)", expanded=True):
                            st.caption("✏️ You can edit any item's **Title** or **Assigned Lot** directly in the table below before committing to Google Sheets.")
                            
                            # Prepare lot options for the editor dropdown
                            available_lot_ids = [l.get("Lot_ID", "") for l in all_lots if l.get("Lot_ID")]
                            if target_lot_id and target_lot_id not in available_lot_ids:
                                available_lot_ids.append(target_lot_id)
                            
                            preview_rows = []
                            for i in new_listings:
                                preview_rows.append({
                                    "Title": i.title, 
                                    "Brand": i.brand or "", 
                                    "Price": f"${i.posted_price:.2f}" if i.posted_price else "$0.00", 
                                    "Category": i.category or "",
                                    "Assigned Lot": i.lot_id or "",
                                })
                            preview_df = pd.DataFrame(preview_rows)

                            lot_editor_options = [""] + available_lot_ids
                            if available_lot_ids:
                                lot_column_config = st.column_config.SelectboxColumn(
                                    "Assigned Lot",
                                    help="Select an inventory lot or choose None to leave unassigned.",
                                    options=lot_editor_options,
                                    format_func=lambda x: "⚪ None (Unassigned)" if not x else x,
                                    required=False,
                                    width="medium",
                                )
                            else:
                                lot_column_config = st.column_config.TextColumn(
                                    "Assigned Lot",
                                    help="Enter a Lot ID (e.g. LOT-001) or leave blank for none.",
                                    required=False,
                                    width="medium",
                                )

                            edited_listings_df = st.data_editor(
                                preview_df,
                                column_config={
                                    "Title": st.column_config.TextColumn(
                                        "Title",
                                        help="Double-click to edit the title if there was an extraction error or typo.",
                                        required=True,
                                        width="large",
                                    ),
                                    "Brand": st.column_config.TextColumn(
                                        "Brand",
                                        disabled=True,
                                        width="small",
                                    ),
                                    "Price": st.column_config.TextColumn(
                                        "Price",
                                        disabled=True,
                                        width="small",
                                    ),
                                    "Category": st.column_config.TextColumn(
                                        "Category",
                                        disabled=True,
                                        width="medium",
                                    ),
                                    "Assigned Lot": lot_column_config,
                                },
                                disabled=["Brand", "Price", "Category"],
                                hide_index=True,
                                num_rows="fixed",
                                width="stretch",
                                key="html_new_listings_editor",
                            )

                    if st.button("🚀 Commit & Synchronize to Google Sheets", type="primary", width="stretch"):
                        try:
                            with st.spinner("Synchronizing to Google Sheets..."):
                                # 1. If new lot, create it in Inventory_Lots if not already created
                                if is_new_lot and target_lot_id:
                                    engine.add_inventory_lot(
                                        lot_id=target_lot_id,
                                        purchase_date=new_purchase_date.strftime("%Y-%m-%d"),
                                        source_vendor=new_vendor,
                                        total_cost=float(new_total_cost),
                                        total_articles=int(new_total_articles),
                                        status=new_status,
                                        notes=new_notes,
                                    )
                                
                                st.session_state.selected_lot = target_lot_id

                                # 2. Apply user edits from st.data_editor to new_listings and parsed_closet
                                if edited_listings_df is not None and not edited_listings_df.empty:
                                    for idx, row in edited_listings_df.iterrows():
                                        if idx < len(new_listings):
                                            edited_title = str(row.get("Title", "")).strip()
                                            raw_lot = row.get("Assigned Lot")
                                            edited_lot = "" if (pd.isna(raw_lot) or str(raw_lot).strip() in ("", "None", "⚪ None (Unassigned)", "nan")) else str(raw_lot).strip()
                                            if edited_title:
                                                new_listings[idx].title = edited_title
                                            new_listings[idx].lot_id = edited_lot

                                # 3. For any other items in parsed_closet, set target_lot_id only if unassigned and target_lot_id is non-empty
                                for it in parsed_closet:
                                    if it not in new_listings:
                                        if not it.lot_id and target_lot_id:
                                            it.lot_id = target_lot_id

                                # 4. Synchronize listings and orders
                                res_orders = engine.sync_orders(parsed_orders, default_lot=target_lot_id, auto_assign_lot=False) if parsed_orders else {"synced_orders": 0}
                                res_closet = engine.sync_listings(parsed_closet, auto_assign_lot=False) if parsed_closet else {"updated": 0, "created": 0}

                                # 5. Record event and clear cache
                                record_html_parse_event(details=f"Web: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items, Lot: {target_lot_id or 'None'})", client=client)
                                log_event(u_email, "html_import", f"HTML import: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items, Lot: {target_lot_id or 'None'})", spreadsheet_id=st.session_state.get("spreadsheet_id"))
                                st.cache_data.clear()

                            lot_msg = f" (New lot {target_lot_id} created in Inventory_Lots)" if is_new_lot else (f" (Assigned to {target_lot_id})" if target_lot_id else " (No lot assigned)")
                            st.success(f"✔ Sync Complete! {res_orders.get('synced_orders', 0)} orders synced, {res_closet.get('created', 0)} listings added{lot_msg}, {res_closet.get('updated', 0)} listings refreshed.")
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:commit", e)
                            if "429" in str(e):
                                st.warning("⏳ Google Sheets rate limit reached (60 read requests/min). Please wait 30 seconds for Google's quota to reset, then click 'Commit & Synchronize' again.")
                            else:
                                st.error(f"Synchronization failed: {e}")

                except Exception as e:
                    log_error(u_email, "sync.py:html_import", e)
                    st.error(f"Error during reconciliation preview: {e}")

    # ---------------------------------------------------------
    # TAB 2: EMAIL SYNC
    # ---------------------------------------------------------
    with tab_email:
        st.markdown("### 📬 Direct Gmail Sync")
        st.caption("Pull incoming sales, buyer offers, and favorites directly from your Gmail account via secure IMAP.")

        e1, e2 = st.columns([1, 1])

        with e1:
            saved_u, saved_p = get_user_email_credentials(u_email)
            fallback_email = u_email if (u_email and "@" in u_email and "local" not in u_email) else ""
            default_user = saved_u or os.getenv("GMAIL_USER", "") or fallback_email
            default_pwd = saved_p or os.getenv("GMAIL_APP_PASSWORD", "")

            gmail_user_input = st.text_input("Gmail Address", value=default_user, placeholder="your-email@gmail.com")
            gmail_pass_input = st.text_input(
                "Google App Password (16 characters)",
                type="password",
                value=default_pwd,
                help="16-character passcode generated at myaccount.google.com/apppasswords"
            )

            col_rem1, col_rem2 = st.columns([1.5, 1], vertical_alignment="center")
            with col_rem1:
                remember_creds = st.checkbox(
                    "💾 Remember password on this machine",
                    value=True,
                    help="Saves your password in local preferences so you don't have to enter it again."
                )
            with col_rem2:
                if saved_p or (os.getenv("GMAIL_APP_PASSWORD") and os.getenv("GMAIL_APP_PASSWORD") == default_pwd):
                    if st.button("🗑️ Forget Password", help="Removes your saved password from local storage"):
                        clear_user_email_credentials(u_email)
                        save_email_credentials_to_env(gmail_user_input, "")
                        st.success("Saved password removed!")
                        time.sleep(1)
                        st.rerun()

            col_m1, col_m2 = st.columns(2)
            with col_m1:
                scan_location = st.selectbox(
                    "Mailbox to Scan",
                    ["All Mail (Inbox + Archived)", "Inbox Only"],
                    index=0,
                    help="All Mail scans both your active inbox and archived Vinted notifications."
                )
            with col_m2:
                max_emails = st.slider("Max emails to scan", min_value=10, max_value=150, value=50)

            folder_param = "ALL" if scan_location.startswith("All Mail") else "INBOX"

            if st.button("🔍 Scan & Preview Vinted Emails", type="primary", width="stretch"):
                if not gmail_user_input or not gmail_pass_input:
                    st.error("Please provide your Gmail address and 16-character App Password.")
                else:
                    if remember_creds:
                        save_user_email_credentials(u_email, gmail_user_input.strip(), gmail_pass_input.strip())
                        save_email_credentials_to_env(gmail_user_input.strip(), gmail_pass_input.strip())

                    with st.spinner(f"Connecting to {gmail_user_input} via IMAP ({scan_location})..."):
                        try:
                            events = fetch_vinted_emails_via_imap(
                                username=gmail_user_input.strip(),
                                password=gmail_pass_input.strip(),
                                folder=folder_param,
                                max_emails=max_emails,
                                search_query='(FROM "vinted")',
                            )
                            if not events:
                                st.session_state.email_sync_preview = None
                                st.info("ℹ️ No Vinted notification emails found. We scanned your entire mailbox (including archived emails) for messages from Vinted. Make sure Vinted notifications are sent to this address.")
                            else:
                                st.session_state.email_sync_preview = events
                                st.session_state.email_sync_user = gmail_user_input.strip()
                                st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:email_scan", e)
                            st.error(f"IMAP Connection failed: {e}")

        with e2:
            with st.container(border=True):
                st.markdown("#### 🔑 Need a Google App Password?")
                st.info(
                    "**Prerequisite:** You must have **2-Step Verification (Two-Factor Authentication / 2FA)** enabled on your Google account. If 2FA is turned off, Google disables App Passwords and displays an error saying the setting is not available.",
                    icon=":material/security:",
                )
                account_email = gmail_user_input.strip() if gmail_user_input else "your Gmail account"
                st.markdown(f"""
                **Step-by-step setup:**
                1. **Enable 2FA:** Visit [Google Security Settings](https://myaccount.google.com/security) and ensure **2-Step Verification** is turned **ON**.
                2. **Open App Passwords:** Go to [Google App Passwords](https://myaccount.google.com/apppasswords) (while signed into `{account_email}`).
                3. **Create Key:** Type `Vinted Tracker` under **App name** $\\rightarrow$ click **Create**.
                4. **Copy & Paste:** Copy the 16-character passcode (e.g. `abcd efgh ijkl mnop`) into the password box on the left.
                
                📘 *For full troubleshooting steps, see the [`GOOGLE_APP_PASSWORD_SETUP.md`](./GOOGLE_APP_PASSWORD_SETUP.md) guide.*
                """)

        # -------------------------------------------------------------
        # Email Sync Preview & Confirmation Block
        # -------------------------------------------------------------
        if st.session_state.get("email_sync_preview"):
            preview_events = st.session_state.email_sync_preview
            st.divider()
            st.markdown(f"#### ⚡ Email Sync Preview ({len(preview_events)} Transactions Detected)")
            st.caption("Review the transactions detected from your Vinted emails before committing them to your Google Sheet.")

            sales_count = len([e for e in preview_events if e.event_type == "sale"])
            offers_count = len([e for e in preview_events if e.event_type == "offer"])
            favs_count = len([e for e in preview_events if e.event_type == "favorite"])

            prev_c1, prev_c2, prev_c3 = st.columns(3)
            prev_c1.metric("💰 Sales to Record", sales_count, border=True)
            prev_c2.metric("💬 Offers Received", offers_count, border=True)
            prev_c3.metric("❤️ Favorites to Increment", favs_count, border=True)

            # Default Inventory Lot Selector for Email Sales
            NO_LOT_OPTION = "⚪ None / No Lot (Unassigned)"
            email_lot_options = [NO_LOT_OPTION] + [
                f"{l['Lot_ID']} — {l.get('Source_Vendor', 'Vendor')} ({l.get('Unit_Cost', '$0.00')}/ea)"
                for l in all_lots if l.get("Lot_ID")
            ]
            selected_email_lot = st.selectbox(
                "Default Inventory Lot for Email Sales:",
                options=email_lot_options,
                help="Select an inventory lot to assign to detected sales, or assign individual lots per-item in the table below.",
                key="email_sync_lot_selectbox",
            )
            target_email_lot = selected_email_lot.split(" — ")[0].strip() if selected_email_lot != NO_LOT_OPTION else ""

            # Interactive Sales Table with Lot, Title, Brand, Category editing
            edited_sale_df = None
            sale_indices = []

            if sales_count > 0:
                st.markdown("##### 🛒 Detected Sales (Editable Preview)")
                st.caption("✏️ You can edit the **Title**, choose an **Assigned Lot**, and verify **Brand** or **Category** before syncing to `Sold_Items`.")

                available_lot_ids = [l.get("Lot_ID", "") for l in all_lots if l.get("Lot_ID")]
                if target_email_lot and target_email_lot not in available_lot_ids:
                    available_lot_ids.append(target_email_lot)
                lot_col_options = [""] + available_lot_ids

                sale_rows = []
                for idx, ev in enumerate(preview_events):
                    if ev.event_type == "sale":
                        sale_indices.append(idx)
                        cur_lot = getattr(ev, "lot_id", "") or target_email_lot
                        cur_brand = getattr(ev, "brand", "") or infer_brand(ev.item_title)
                        cur_cat = getattr(ev, "category", "") or (infer_clothing_category(ev.item_title) if ev.item_title else "Other Apparel")
                        sale_rows.append({
                            "Title": ev.item_title or f"Vinted Sale {ev.order_id}",
                            "Assigned Lot": cur_lot,
                            "Brand": cur_brand,
                            "Category": cur_cat,
                            "Price": f"${ev.sold_price:.2f}",
                            "Buyer": f"@{ev.buyer_username}" if ev.buyer_username else "-",
                            "Order ID": ev.order_id or "-",
                            "Date": ev.timestamp.strftime("%Y-%m-%d"),
                        })

                sale_df = pd.DataFrame(sale_rows)
                edited_sale_df = st.data_editor(
                    sale_df,
                    column_config={
                        "Title": st.column_config.TextColumn("Title", required=True, width="large"),
                        "Assigned Lot": st.column_config.SelectboxColumn("Assigned Lot", options=lot_col_options, format_func=lambda x: "⚪ None (Unassigned)" if not x else x, width="medium"),
                        "Brand": st.column_config.TextColumn("Brand", width="medium"),
                        "Category": st.column_config.SelectboxColumn("Category", options=["Tops & Shirts", "Outerwear", "Pants & Jeans", "Sweaters & Hoodies", "Dresses & Skirts", "Footwear", "Accessories", "Other Apparel"], width="medium"),
                        "Price": st.column_config.TextColumn("Price", disabled=True, width="small"),
                        "Buyer": st.column_config.TextColumn("Buyer", disabled=True, width="small"),
                        "Order ID": st.column_config.TextColumn("Order ID", disabled=True, width="medium"),
                        "Date": st.column_config.TextColumn("Date", disabled=True, width="small"),
                    },
                    hide_index=True,
                    num_rows="fixed",
                    width="stretch",
                    key="email_sales_editor",
                )

            non_sales = [e for e in preview_events if e.event_type != "sale"]
            if non_sales:
                with st.expander(f"View Other Detected Events ({len(non_sales)} offers & favorites)", expanded=False):
                    st.dataframe(pd.DataFrame([{
                        "Type": ev.event_type.upper(),
                        "Item": ev.item_title or ev.vinted_id,
                        "Detail": f"${ev.offer_price:.2f}" if ev.event_type == "offer" else "Favorited",
                        "User": f"@{ev.buyer_username}" if ev.buyer_username else "-",
                        "Date": ev.timestamp.strftime("%Y-%m-%d %H:%M"),
                    } for ev in non_sales]), width="stretch")

            action_c1, action_c2 = st.columns([1.5, 1])
            with action_c1:
                if st.button("🚀 Confirm & Sync to Google Sheets", type="primary", width="stretch", key="btn_confirm_email_sync"):
                    with st.spinner("Synchronizing email events to Google Sheets..."):
                        try:
                            # Apply any edits from st.data_editor to preview_events
                            if edited_sale_df is not None and not edited_sale_df.empty:
                                for s_idx, df_idx in enumerate(sale_indices):
                                    if s_idx < len(edited_sale_df):
                                        row = edited_sale_df.iloc[s_idx]
                                        ev = preview_events[df_idx]
                                        ed_title = str(row.get("Title", "")).strip()
                                        if ed_title:
                                            ev.item_title = ed_title
                                        raw_lot = row.get("Assigned Lot")
                                        ev.lot_id = "" if (pd.isna(raw_lot) or str(raw_lot).strip() in ("", "None", "⚪ None", "⚪ None (Unassigned)")) else str(raw_lot).strip()
                                        ev.brand = str(row.get("Brand", "")).strip()
                                        ev.category = str(row.get("Category", "")).strip()

                            engine = get_sheets_sync_engine(client)
                            results = engine.process_email_batch(preview_events, default_lot=target_email_lot)
                            processed = len([r for r in results if r.get("status") not in ("ignored", "no_listings")])
                            log_event(
                                u_email,
                                "email_sync",
                                f"IMAP sync: {processed} events from {st.session_state.get('email_sync_user', gmail_user_input)}",
                                spreadsheet_id=st.session_state.get("spreadsheet_id"),
                            )
                            st.cache_data.clear()
                            st.session_state.email_sync_preview = None
                            st.success(f"✔ Successfully synced {processed} email transactions to your Google Sheet with full lot, brand, and category assignments!")
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:email_sync_confirm", e)
                            st.error(f"Sync failed: {e}")

            with action_c2:
                if st.button("❌ Cancel / Clear Preview", type="secondary", width="stretch", key="btn_cancel_email_sync"):
                    st.session_state.email_sync_preview = None
                    st.rerun()

    # ---------------------------------------------------------
    # TAB 3: SHIPPING SUPPLIES
    # ---------------------------------------------------------
    with tab_supplies:
        st.markdown("### 🏷️ Shipping Supplies & Packaging Costs")
        st.caption("Manage boxes, poly mailers, labels, and tape. The global weighted average packaging cost is automatically deducted from every sale in `Sold_Items` to compute true net profits.")

        sup_engine = get_sheets_sync_engine(client)
        try:
            sup_data = get_cached_supplies(sheet_id, user_creds)
        except Exception as e:
            if "429" in str(e):
                st.warning("⏳ Google Sheets rate limit reached. Waiting for quota reset...")
                st.button("🔄 Retry Supplies", on_click=lambda: st.cache_data.clear(), key="retry_sup_btn")
            sup_data = {"supplies": [], "avg_cost_per_shipment": 0.24}
        active_supplies = sup_data.get("supplies", [])
        avg_cost_val = sup_data.get("avg_cost_per_shipment", 0.24)

        # KPI Metrics Row
        tot_units = sum(int(float(s.get("Quantity", 0))) for s in active_supplies if s.get("Quantity"))
        tot_spent = sum(parse_price(s.get("Total_Cost", 0)) for s in active_supplies if s.get("Total_Cost"))

        sk1, sk2, sk3, sk4 = st.columns(4)
        with sk1:
            st.metric("Active Supplies", len(active_supplies), border=True)
        with sk2:
            st.metric("Total Units Stocked", f"{tot_units:,} units", border=True)
        with sk3:
            st.metric("Total Spend", f"${tot_spent:,.2f}", border=True)
        with sk4:
            st.metric("Avg Packaging Cost / Order", f"${avg_cost_val:.2f}", help="Deducted automatically from each Vinted sale to calculate Net Profit in Sold_Items.", border=True)

        st.divider()

        # Section 1: Form to Add or Update Supply
        with st.container(border=True):
            st.markdown("#### ➕ Add or Update Shipping Supply")

            next_sup_id = sup_engine.get_next_supply_id(cached_supplies=active_supplies)
            sup_mode_options = [f"➕ New Supply ({next_sup_id})"] + [
                f"{s['Supply_ID']} — {s.get('Item_Description') or 'Supply'} ({s.get('Vendor') or 'Vendor'})"
                for s in active_supplies
            ]

            selected_sup_mode = st.selectbox(
                "Action / Select Supply to Edit",
                options=sup_mode_options,
                key="select_supply_mode",
            )

            is_editing_existing = not selected_sup_mode.startswith("➕ New Supply")
            if is_editing_existing:
                selected_sup_id = selected_sup_mode.split(" — ")[0].strip()
                existing_item = next((s for s in active_supplies if s["Supply_ID"] == selected_sup_id), {})
                default_date_str = existing_item.get("Purchase_Date", "")
                try:
                    default_date = datetime.strptime(default_date_str, "%Y-%m-%d").date() if default_date_str else date.today()
                except ValueError:
                    default_date = date.today()
                default_desc = existing_item.get("Item_Description", "")
                default_vendor = existing_item.get("Vendor", "")
                default_qty = int(float(existing_item.get("Quantity", 1))) if existing_item.get("Quantity") else 100
                default_cost = parse_price(existing_item.get("Total_Cost", 0)) or 18.99
                form_sup_id = selected_sup_id
            else:
                default_date = date.today()
                default_desc = ""
                default_vendor = "Amazon"
                default_qty = 100
                default_cost = 18.99
                form_sup_id = next_sup_id

            sc1, sc2 = st.columns(2)
            with sc1:
                input_sup_id = st.text_input("Supply ID", value=form_sup_id, disabled=is_editing_existing, help="e.g. SUP-01, SUP-02")
                input_sup_date = st.date_input("Purchase Date", value=default_date, key="sup_date_input")
                input_sup_desc = st.text_input("Item Description", value=default_desc, placeholder="e.g. 10x13 Poly Mailers (Pack of 100)", key="sup_desc_input")
            with sc2:
                input_sup_vendor = st.text_input("Vendor / Store", value=default_vendor, placeholder="e.g. Amazon, Uline, Target", key="sup_vendor_input")
                input_sup_qty = st.number_input("Quantity in Package", min_value=1, value=default_qty, step=1, key="sup_qty_input")
                input_sup_cost = st.number_input("Total Cost Paid ($)", min_value=0.0, value=float(default_cost), step=1.0, key="sup_cost_input")

            calc_sup_unit = (input_sup_cost / input_sup_qty) if input_sup_qty > 0 else 0.0
            st.info(f"💡 Calculated Unit Packaging Cost: **${calc_sup_unit:.3f} per unit** (`${input_sup_cost:.2f} ÷ {input_sup_qty} units`)")

            if st.button("💾 Save Supply to Spreadsheet", type="primary", width="stretch", key="btn_save_supply"):
                with st.spinner(f"Saving supply {form_sup_id} to Google Sheets..."):
                    res = sup_engine.save_shipping_supply(
                        supply_id=form_sup_id,
                        purchase_date=input_sup_date.strftime("%Y-%m-%d"),
                        item_description=input_sup_desc,
                        vendor=input_sup_vendor,
                        quantity=int(input_sup_qty),
                        total_cost=float(input_sup_cost),
                    )
                    log_event(u_email, "supply_saved", f"Supply {form_sup_id}: {input_sup_desc} ({input_sup_qty} @ ${input_sup_cost})", spreadsheet_id=st.session_state.get("spreadsheet_id"))
                    st.cache_data.clear()

                st.success(f"✔ Supply {form_sup_id} saved successfully! Global packaging deduction updated in Google Sheets.")
                st.balloons()
                time.sleep(1.2)
                st.rerun()

        # Section 2: Current Inventory Table
        st.markdown("#### 📋 Current Shipping Supplies in Sheet")
        if active_supplies:
            display_supplies = []
            for s in active_supplies:
                q_num = int(float(s.get("Quantity", 0))) if s.get("Quantity") else 0
                c_num = parse_price(s.get("Total_Cost", 0))
                u_str = s.get("Unit_Cost") or (f"${c_num / q_num:.3f}" if q_num > 0 else "$0.00")
                display_supplies.append({
                    "Supply ID": s.get("Supply_ID"),
                    "Purchase Date": s.get("Purchase_Date"),
                    "Item Description": s.get("Item_Description"),
                    "Vendor": s.get("Vendor"),
                    "Quantity": q_num,
                    "Total Cost": f"${c_num:.2f}",
                    "Unit Cost": u_str,
                })
            st.dataframe(pd.DataFrame(display_supplies), width="stretch")
        else:
            st.info("No shipping supplies recorded yet.")


if __name__ == "__main__":
    render_sync_page()
else:
    render_sync_page()
