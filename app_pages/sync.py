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
from src.html_parser import infer_brand, infer_clothing_category
from src.email_parser import fetch_vinted_emails_via_imap, get_last_scan_date
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
    if (
        not hasattr(EngineClass, "reconcile_missing_sold_metadata")
        or not hasattr(EngineClass, "clean_blank_rows")
        or not hasattr(EngineClass, "find_duplicate_groups")
        or not hasattr(EngineClass, "apply_sync_plan")
    ):
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


@st.cache_data(max_entries=4, show_spinner="Parsing uploaded Vinted files...")
def get_cached_snapshot(file_payload: tuple, default_lot: str):
    """Parses and merges uploaded HTML files once per unique upload (keyed by file content)."""
    from src.html_parser import build_snapshot
    return build_snapshot(list(file_payload), default_lot=default_lot)


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_sheet_state(sheet_id: str, _creds):
    """Reads Item_Listings, Sold_Items, Hidden_Items and Cancelled_Orders in 1 batch call (60-second cache)."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    return get_sheets_sync_engine(client).read_sheet_state()


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
    c_head, c_ref, c_btn = st.columns([4, 1.2, 1.2], vertical_alignment="center")
    with c_head:
        st.markdown("##### Sync Status & Metrics")
    with c_ref:
        if st.button("Refresh Live", icon=":material/refresh:", width="stretch", help="Fetch latest live counts from Google Sheets", key="sync_refresh_live_btn"):
            st.cache_data.clear()
            st.rerun()
    with c_btn:
        with st.popover("Card Style", icon=":material/palette:", help="Customize card background, text color, and themes"):
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
            {"label": "Active Listings", "icon": "sell", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#161b22", "border": "#30363d", "accent": "#58a6ff", "text": "#f0f6fc"},
            {"label": "Sold Items", "icon": "payments", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#161b22", "border": "#30363d", "accent": "#3fb950", "text": "#f0f6fc"},
            {"label": "Last HTML Import", "icon": "language", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#161b22", "border": "#30363d", "accent": "#d29922", "text": "#f0f6fc"},
            {"label": "Last Email Parsed", "icon": "mail", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#161b22", "border": "#30363d", "accent": "#bc8cff", "text": "#f0f6fc"},
        ]
    elif style_mode == "Vibrant Dark":
        cards = [
            {"label": "Active Listings", "icon": "sell", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#0c1e3d", "border": "#2563eb", "accent": "#60a5fa", "text": "#f8fafc"},
            {"label": "Sold Items", "icon": "payments", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#062e24", "border": "#059669", "accent": "#34d399", "text": "#f8fafc"},
            {"label": "Last HTML Import", "icon": "language", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#361e05", "border": "#d97706", "accent": "#fbbf24", "text": "#f8fafc"},
            {"label": "Last Email Parsed", "icon": "mail", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#260e42", "border": "#7c3aed", "accent": "#c084fc", "text": "#f8fafc"},
        ]
    elif style_mode == "Custom Colors":
        c_bg = st.session_state.get("sync_card_custom_bg", "#0f172a")
        c_text = st.session_state.get("sync_card_custom_text", "#f8fafc")
        c_border = st.session_state.get("sync_card_custom_border", "#3b82f6")
        cards = [
            {"label": "Active Listings", "icon": "sell", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Sold Items", "icon": "payments", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Last HTML Import", "icon": "language", "value": h_display, "sub": h_sub, "is_date": True, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
            {"label": "Last Email Parsed", "icon": "mail", "value": e_display, "sub": e_sub, "is_date": True, "bg": c_bg, "border": c_border, "accent": c_border, "text": c_text},
        ]
    else:  # "Deep Slate (Dark)" (Default)
        cards = [
            {"label": "Active Listings", "icon": "sell", "value": f"{active_cnt:,}", "sub": "In Google Sheets", "is_date": False, "bg": "#0f172a", "border": "#1e3a8a", "accent": "#60a5fa", "text": "#f8fafc"},
            {"label": "Sold Items", "icon": "payments", "value": f"{sold_cnt:,}", "sub": "Completed sales", "is_date": False, "bg": "#0f172a", "border": "#065f46", "accent": "#34d399", "text": "#f8fafc"},
            {"label": "Last HTML Import", "icon": "language", "value": h_display, "sub": h_sub, "is_date": True, "bg": "#0f172a", "border": "#854d0e", "accent": "#fbbf24", "text": "#f8fafc"},
            {"label": "Last Email Parsed", "icon": "mail", "value": e_display, "sub": e_sub, "is_date": True, "bg": "#0f172a", "border": "#581c87", "accent": "#c084fc", "text": "#f8fafc"},
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
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200');
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
        font-family: 'Material Symbols Rounded', sans-serif;
        font-size: 1.25rem;
        line-height: 1;
        display: inline-block;
        vertical-align: middle;
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
    st.header("Thread Count Operations & Sync Hub")
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
            st.warning("Google Sheets rate limit reached (60 read requests/min). Please wait 30 seconds for Google's quota to reset.", icon=":material/hourglass_top:")
            st.button("Retry Connection", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), key="retry_rate_limit_btn")
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
        st.warning("Could not connect to Google Spreadsheet. Check permissions or ensure your spreadsheet is shared with your account.", icon=":material/warning:")
        return

    # Quick Maintenance Tools
    with st.expander("Spreadsheet Tools", icon=":material/build:", expanded=False):
        c_tool1, c_tool2, c_tool3, c_tool4, c_tool5, c_tool6 = st.columns(6)
        with c_tool1:
            if st.button("Clean Blank Rows", type="secondary", width="stretch", icon=":material/cleaning_services:", key="clean_blank_rows_btn"):
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
                        st.success(f"Purged {total_cleaned} blank row(s) from spreadsheet ({details})!", icon=":material/check_circle:")
                    else:
                        st.info("No blank rows detected in your spreadsheet. All clean!", icon=":material/check_circle:")
                    time.sleep(1.2)
                    st.rerun()
        with c_tool2:
            saved_u, saved_p = get_user_email_credentials(u_email)
            active_email_user = saved_u or os.getenv("GMAIL_USER", "") or st.session_state.get("gmail_user_input", "") or st.session_state.get("email_sync_user", "")
            active_email_pass = saved_p or os.getenv("GMAIL_APP_PASSWORD", "") or st.session_state.get("gmail_pass_input", "")

            if st.button("Backfill Missing Metadata in Sold", type="secondary", width="stretch", icon=":material/auto_fix_high:", key="backfill_metadata_btn"):
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
                            f"Enriched {res['fixed_rows']} sold items{email_info}! "
                            f"({res['titles_fixed']} titles restored, {res['lots_assigned']} lots assigned, "
                            f"{res['brands_fixed']} brands, {res['categories_fixed']} categories)",
                            icon=":material/check_circle:",
                        )
                    else:
                        email_scanned_msg = f" (Scanned {res.get('emails_scanned', 0)} emails)" if res.get('emails_scanned', 0) > 0 else ""
                        st.info(f"All items in Sold_Items already have full metadata!{email_scanned_msg}", icon=":material/check_circle:")
                    time.sleep(1.5)
                    st.rerun()

            if active_email_user:
                st.caption(f":material/mail: Gmail: `{active_email_user}` (re-scans `Vinted-Processed` emails)")
            else:
                st.caption(":material/lightbulb: Enter Gmail in **Direct Email Sync** tab to re-scan `Vinted-Processed` emails.")
        with c_tool3:
            if st.button("Check Duplicates", type="secondary", width="stretch", icon=":material/difference:", key="quick_check_dups_btn"):
                st.session_state.duplicate_scan_trigger = True
                st.rerun()
            st.caption("Scan sheets for duplicate items & placeholder rows.")
        with c_tool4:
            if st.button("Provision Missing Tabs", type="secondary", width="stretch", icon=":material/tab:", key="ensure_tabs_btn"):
                with st.spinner("Checking and provisioning all template worksheets..."):
                    engine = get_sheets_sync_engine(client)
                    created = engine.ensure_required_tabs()
                    st.cache_data.clear()
                    if created:
                        st.success(f"Provisioned {len(created)} missing tab(s): {', '.join(created)} with schema headers!", icon=":material/check_circle:")
                    else:
                        st.info("All required tabs (including Cancelled_Orders & Hidden_Items) already exist!", icon=":material/check_circle:")
                    time.sleep(1.2)
                    st.rerun()
            st.caption("Auto-create Cancelled_Orders, Hidden_Items, etc.")
        with c_tool5:
            if st.button("Force Refresh Data", type="secondary", width="stretch", icon=":material/refresh:", key="force_refresh_data_btn"):
                st.cache_data.clear()
                st.success("Cache cleared! Reloading fresh data...", icon=":material/check_circle:")
                time.sleep(0.5)
                st.rerun()
        with c_tool6:
            with st.popover("Wipe Sheet", icon=":material/delete_forever:", help="Wipe all inventory and sales data to start completely fresh"):
                st.markdown("**Danger Zone: Wipe Entire Sheet**")
                st.caption("Resets `Item_Listings`, `Sold_Items`, `Hidden_Items`, and `Cancelled_Orders` to 0 rows. Resets `Inventory_Lots` to `LOT-001`.")
                confirm_wipe = st.checkbox("Confirm sheet reset", key="confirm_wipe_sheet_chk")
                if st.button("Wipe & Reset", type="primary", disabled=not confirm_wipe, key="exec_wipe_sheet_btn"):
                    with st.spinner("Wiping spreadsheet and re-initializing pristine templates..."):
                        engine = get_sheets_sync_engine(client)
                        engine.wipe_and_reset_sheet()
                        st.cache_data.clear()
                        st.success("Spreadsheet completely wiped and reset to a fresh template!", icon=":material/check_circle:")
                        time.sleep(1.2)
                        st.rerun()
            st.caption("Start fresh with empty catalog.")

    # -------------------------------------------------------------------------
    # Main Tabs: HTML Import, Email Sync, Duplicate Checker, Supplies
    # -------------------------------------------------------------------------
    tab_html, tab_email, tab_dups, tab_supplies = st.tabs([
        ":material/upload_file: HTML Batch Import & Reconciliation",
        ":material/mail: Direct Email Sync",
        ":material/difference: Duplicate Checker & Resolver",
        ":material/inventory_2: Shipping Supplies",
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
                st.warning("Google Sheets quota reached. Waiting for reset...", icon=":material/hourglass_empty:")
                st.button("Retry Lots", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), key="retry_lots_btn")
            all_lots = []

        # -----------------------------------------------------------------
        # 1. Inventory Lot Assignment (Always Visible)
        # -----------------------------------------------------------------
        with st.container(border=True):
            st.markdown("### Inventory Lot Assignment")
            st.caption("Choose which inventory lot new items should be assigned to, leave unassigned, or register a new lot.")

            NO_LOT_OPTION = "None / No Lot (Unassigned)"
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

            lot_options.append("Add New Lot...")

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
                help="Select an existing lot from your sheet, choose 'None / No Lot' to leave unassigned, or 'Add New Lot...' to register a new haul.",
                key="sync_lot_selectbox",
            )

            is_new_lot = (selected_option == "Add New Lot...")
            is_none_lot = (selected_option == NO_LOT_OPTION)

            if is_new_lot:
                with st.container(border=True):
                    st.markdown("#### Define New Inventory Lot")
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
                    st.info(f"Calculated Unit Cost per item: **${calc_unit_cost:.2f}** (`${new_total_cost:.2f} ÷ {new_total_articles} articles`)", icon=":material/lightbulb:")

                    new_notes = st.text_input("Notes", placeholder="e.g. 50 assorted fall sweaters and flannels", key="input_new_lot_notes")
                    target_lot_id = new_lot_id.strip().upper() if new_lot_id else next_suggested_id

                    def on_cancel_new_lot():
                        st.session_state["sync_lot_selectbox"] = NO_LOT_OPTION

                    btn_c1, btn_c2 = st.columns([2, 1], vertical_alignment="bottom")
                    with btn_c1:
                        if st.button("Save New Lot to Spreadsheet Now", icon=":material/save:", type="secondary", width="stretch"):
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
                                st.warning(f"Lot {target_lot_id} already exists in your spreadsheet.", icon=":material/warning:")
                            else:
                                st.success(f"Lot {target_lot_id} successfully saved to Inventory_Lots!", icon=":material/check_circle:")
                            time.sleep(1.2)
                            st.rerun()
                    with btn_c2:
                        st.button("Cancel", on_click=on_cancel_new_lot, type="tertiary", icon=":material/close:", width="stretch")
            elif is_none_lot:
                target_lot_id = ""
                st.session_state.selected_lot = ""
                st.info("**No Lot Selected**: New items will be imported without an assigned inventory lot (Unit Cost = $0.00). You can choose lots individually in the preview editor below or assign them later in Google Sheets.", icon=":material/info:")
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

        with st.expander("How to export your FULL catalog (180+ items) from Vinted", icon=":material/info:", expanded=False):
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
                
                *Pro Tip: 1-Click Auto-Scroll Bookmarklet:*
                Create a bookmark in your browser with this URL to auto-scroll any Vinted page with 1 click:
                ```javascript
                javascript:(function(){let lastHeight=0,repeats=0;let t=setInterval(()=>{window.scrollBy(0,1500);let h=document.body.offsetHeight;if(h===lastHeight){repeats++;if(repeats>=4){clearInterval(t);alert('All Vinted items loaded! Press Cmd+S (Ctrl+S) and select "Webpage, Complete" or "HTML Only" to save.');}}else{lastHeight=h;repeats=0;}},300);})();
                ```
                *Or paste this snippet directly into Developer Tools Console (`Cmd+Option+J` on Mac or `Ctrl+Shift+J` on Windows):*
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
            from src.sheets.reconcile import build_sync_plan

            file_payload = tuple((f.name, f.getvalue()) for f in uploaded_files)
            snap = get_cached_snapshot(file_payload, target_lot_id)

            st.markdown("#### Detected Files & Content Analysis")
            st.dataframe(pd.DataFrame(snap.files), width="stretch", hide_index=True)

            # -------------------------------------------------------------
            # 3. Reconciliation Preview: the exact plan that Commit will write
            # -------------------------------------------------------------
            if snap.items or snap.orders:
                st.divider()
                lot_preview_header = f" (Default Lot: {target_lot_id})" if target_lot_id else " (Default Lot: None)"
                st.markdown(f"#### Reconciliation Preview{lot_preview_header}")

                try:
                    state = get_cached_sheet_state(sheet_id, user_creds)
                    plan = build_sync_plan(snap, state, default_lot=target_lot_id)
                    before, after = plan.counts_before, plan.counts_after

                    st.caption("Totals in your Google Sheet after this sync (change vs. now):")
                    k1, k2, k3, k4 = st.columns(4)
                    for col, key, label in ((k1, "active", "Active Listings"), (k2, "sold", "Sold Items"), (k3, "hidden", "Hidden Items"), (k4, "cancelled", "Cancelled Orders")):
                        col.metric(label, after[key], after[key] - before[key], border=True)

                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("New Active", len(plan.new_listings), border=True)
                    c2.metric("New Sold", len(plan.new_sold), border=True)
                    c3.metric("Active → Sold", len(plan.listing_to_sold) + len(plan.hidden_to_sold), border=True)
                    c4.metric("→ Hidden", len(plan.listing_to_hidden) + len(plan.new_hidden), border=True)
                    c5.metric("Updated / Enriched", plan.updated_listings + plan.enriched_sold, border=True)

                    if plan.warnings:
                        with st.expander(f"Notes ({len(plan.warnings)})", icon=":material/info:", expanded=False):
                            for w in plan.warnings:
                                st.markdown(f"- {w}")

                    if plan.new_cancelled:
                        with st.expander(f"Cancelled Orders to Record ({len(plan.new_cancelled)})", icon=":material/cancel:", expanded=False):
                            st.caption("Cancelled items are moved to **Cancelled_Orders** and kept out of Sold_Items. Bundle contents and cancellation dates are filled in by email sync.")
                            st.dataframe(pd.DataFrame(plan.new_cancelled), width="stretch", hide_index=True)
                    if plan.removed_for_cancellation:
                        st.caption(f"Removing {len(plan.removed_for_cancellation)} cancelled item(s) from Sold_Items: {', '.join(plan.removed_for_cancellation)}")

                    if plan.listing_to_sold or plan.hidden_to_sold:
                        with st.expander(f"Active → Sold ({len(plan.listing_to_sold) + len(plan.hidden_to_sold)})", icon=":material/sell:", expanded=False):
                            st.dataframe(pd.DataFrame({"Title": plan.listing_to_sold + plan.hidden_to_sold}), width="stretch", hide_index=True)

                    if plan.new_sold:
                        with st.expander(f"New Sold Items to Add ({len(plan.new_sold)})", icon=":material/shopping_bag:", expanded=False):
                            st.caption("Sale dates are left blank; email sync fills them in.")
                            st.dataframe(pd.DataFrame(plan.new_sold), width="stretch", hide_index=True)

                    hidden_moves = plan.listing_to_hidden + plan.new_hidden
                    if hidden_moves or plan.hidden_to_listing:
                        with st.expander(f"Hidden Changes ({len(hidden_moves) + len(plan.hidden_to_listing)})", icon=":material/visibility_off:", expanded=False):
                            rows = [{"Title": t, "Change": "→ Hidden"} for t in hidden_moves]
                            rows += [{"Title": t, "Change": "Hidden → Active"} for t in plan.hidden_to_listing]
                            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

                    edited_listings_df = None
                    if plan.new_listings:
                        with st.expander(f"New Listings to Add ({len(plan.new_listings)} items — Editable)", icon=":material/edit_note:", expanded=True):
                            st.caption("Edit any item's **Title** or **Assigned Lot** before committing.")
                            available_lot_ids = [l.get("Lot_ID", "") for l in all_lots if l.get("Lot_ID")]
                            if target_lot_id and target_lot_id not in available_lot_ids:
                                available_lot_ids.append(target_lot_id)
                            lot_editor_options = [""] + sorted(set(available_lot_ids))
                            preview_df = pd.DataFrame(plan.new_listings)
                            preview_df["Price"] = preview_df["Price"].map(lambda p: f"${float(p or 0):.2f}")
                            edited_listings_df = st.data_editor(
                                preview_df,
                                column_config={
                                    "Vinted ID": st.column_config.TextColumn("Vinted ID", disabled=True, width="small"),
                                    "Title": st.column_config.TextColumn("Title", required=True, width="large"),
                                    "Assigned Lot": st.column_config.SelectboxColumn(
                                        "Assigned Lot",
                                        options=lot_editor_options,
                                        format_func=lambda x: "None (Unassigned)" if not x else x,
                                        required=False,
                                        width="medium",
                                    ),
                                },
                                disabled=["Vinted ID", "Brand", "Price", "Category"],
                                hide_index=True,
                                num_rows="fixed",
                                width="stretch",
                                key="html_new_listings_editor",
                            )

                    if not plan.has_changes:
                        st.success("Your sheet already matches these files. Nothing to sync.", icon=":material/check_circle:")

                    if st.button("Commit & Synchronize to Google Sheets", icon=":material/cloud_upload:", type="primary", width="stretch", disabled=not plan.has_changes and not is_new_lot):
                        try:
                            with st.spinner("Synchronizing to Google Sheets..."):
                                engine.ensure_required_tabs()
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

                                overrides = {}
                                if edited_listings_df is not None and not edited_listings_df.empty:
                                    for _, row in edited_listings_df.iterrows():
                                        raw_lot = row.get("Assigned Lot")
                                        lot_val = "" if (pd.isna(raw_lot) or str(raw_lot).strip() in ("", "None", "None (Unassigned)", "nan")) else str(raw_lot).strip()
                                        overrides[str(row["Vinted ID"])] = {"title": str(row.get("Title", "")).strip(), "lot": lot_val}

                                # Re-read the sheet so the plan is applied against the latest data
                                fresh_state = engine.read_sheet_state()
                                final_plan = build_sync_plan(snap, fresh_state, default_lot=target_lot_id, listing_overrides=overrides)
                                result = engine.apply_sync_plan(final_plan, fresh_state)

                                n_orders = len(snap.orders)
                                details = f"{len(uploaded_files)} files ({len(snap.items)} closet items, {n_orders} orders, Lot: {target_lot_id or 'None'})"
                                record_html_parse_event(details=f"Web: {details}", client=client)
                                log_event(u_email, "html_import", f"HTML import: {details}", spreadsheet_id=st.session_state.get("spreadsheet_id"))
                                st.cache_data.clear()

                            st.success(
                                f"Sync complete! Active {result['active']} · Sold {result['sold']} · Hidden {result['hidden']} · Cancelled {result['cancelled']}",
                                icon=":material/check_circle:",
                            )
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:commit", e)
                            if "429" in str(e):
                                st.warning("Google Sheets rate limit reached (60 read requests/min). Please wait 30 seconds for Google's quota to reset, then click 'Commit & Synchronize' again.", icon=":material/warning:")
                            else:
                                st.error(f"Synchronization failed: {e}")

                except Exception as e:
                    log_error(u_email, "sync.py:html_import", e)
                    if "429" in str(e):
                        st.warning("Google Sheets rate limit reached. Wait 30 seconds and click **Refresh Live**.", icon=":material/hourglass_top:")
                    else:
                        st.error(f"Error during reconciliation preview: {e}")

    # ---------------------------------------------------------
    # TAB 2: EMAIL SYNC
    # ---------------------------------------------------------
    with tab_email:
        st.markdown("### Direct Gmail Sync")
        st.caption("Pull incoming sales, buyer offers, and favorites directly from your Gmail account via secure IMAP.")

        e1, e2 = st.columns([1, 1])

        with e1:
            saved_u, saved_p = get_user_email_credentials(u_email)
            fallback_email = u_email if (u_email and "@" in u_email and "local" not in u_email) else ""
            default_user = saved_u or os.getenv("GMAIL_USER", "") or fallback_email
            default_pwd = saved_p or os.getenv("GMAIL_APP_PASSWORD", "")

            gmail_user_input = st.text_input("Gmail Address", value=default_user, placeholder="your-email@gmail.com", key="gmail_user_input")
            gmail_pass_input = st.text_input(
                "Google App Password (16 characters)",
                type="password",
                value=default_pwd,
                help="16-character passcode generated at myaccount.google.com/apppasswords",
                key="gmail_pass_input",
            )

            col_rem1, col_rem2 = st.columns([1.5, 1], vertical_alignment="center")
            with col_rem1:
                remember_creds = st.checkbox(
                    "Remember password on this machine",
                    value=True,
                    help="Saves your password in local preferences so you don't have to enter it again."
                )
            with col_rem2:
                if saved_p or (os.getenv("GMAIL_APP_PASSWORD") and os.getenv("GMAIL_APP_PASSWORD") == default_pwd):
                    if st.button("Forget Password", icon=":material/delete:", help="Removes your saved password from local storage"):
                        clear_user_email_credentials(u_email)
                        save_email_credentials_to_env(gmail_user_input, "")
                        st.success("Saved password removed!", icon=":material/check_circle:")
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
                max_emails = st.slider("Max emails to scan", min_value=10, max_value=500, value=150, step=10)

            folder_param = "ALL" if scan_location.startswith("All Mail") else "INBOX"

            # Date Range Filter Selector
            last_scan_dt = get_last_scan_date()
            default_scan_label = f"Since Last Scan ({last_scan_dt.strftime('%b %d, %Y')})" if last_scan_dt else "Since Last Scan (Default)"

            date_filter_choices = [
                default_scan_label,
                "Past 7 Days",
                "Past 30 Days",
                "All Time",
                "Custom Date Range"
            ]

            selected_date_filter = st.selectbox(
                "Date Range to Scan",
                options=date_filter_choices,
                index=0,
                help="Defaults to scanning emails received since your last sync, or choose a broader range."
            )

            since_date = None
            until_date = None
            if selected_date_filter == default_scan_label:
                since_date = last_scan_dt or (date.today() - timedelta(days=30))
            elif selected_date_filter == "Past 7 Days":
                since_date = date.today() - timedelta(days=7)
            elif selected_date_filter == "Past 30 Days":
                since_date = date.today() - timedelta(days=30)
            elif selected_date_filter == "All Time":
                since_date = None
            elif selected_date_filter == "Custom Date Range":
                c_d1, c_d2 = st.columns(2)
                with c_d1:
                    since_date = st.date_input("From Date", value=date.today() - timedelta(days=14))
                with c_d2:
                    until_date = st.date_input("To Date", value=date.today())

            if st.button("Scan & Preview Vinted Emails", icon=":material/search:", type="primary", width="stretch"):
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
                                since_date=since_date,
                                until_date=until_date,
                            )
                            if not events:
                                st.session_state.email_sync_preview = None
                                st.info("No Vinted notification emails found. We scanned your entire mailbox (including archived emails) for messages from Vinted. Make sure Vinted notifications are sent to this address.", icon=":material/info:")
                            else:
                                st.session_state.email_sync_preview = events
                                st.session_state.email_sync_user = gmail_user_input.strip()
                                st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:email_scan", e)
                            st.error(f"IMAP Connection failed: {e}")

        with e2:
            with st.container(border=True):
                st.markdown("#### Need a Google App Password?")
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
                
                *For full troubleshooting steps, see the [`GOOGLE_APP_PASSWORD_SETUP.md`](./GOOGLE_APP_PASSWORD_SETUP.md) guide.*
                """)

        # -------------------------------------------------------------
        # Email Sync Preview & Confirmation Block
        # -------------------------------------------------------------
        if st.session_state.get("email_sync_preview"):
            preview_events = st.session_state.email_sync_preview
            st.divider()
            st.markdown(f"#### Email Sync Preview ({len(preview_events)} Transactions Detected)")
            st.caption("Review the transactions detected from your Vinted emails before committing them to your Google Sheet.")

            sales_count = len([e for e in preview_events if e.event_type in ("sale", "bundle_sale")])
            offers_count = len([e for e in preview_events if e.event_type == "offer"])
            favs_count = len([e for e in preview_events if e.event_type == "favorite"])
            canc_count = len([e for e in preview_events if e.event_type == "cancellation"])

            prev_c1, prev_c2, prev_c3, prev_c4 = st.columns(4)
            prev_c1.metric("Sales to Record", sales_count, border=True)
            prev_c2.metric("Cancellations", canc_count, border=True)
            prev_c3.metric("Offers Received", offers_count, border=True)
            prev_c4.metric("Favorites to Increment", favs_count, border=True)

            if canc_count > 0:
                with st.expander(f"View Cancellations Detected ({canc_count} orders)", icon=":material/cancel:", expanded=False):
                    canc_evs = [e for e in preview_events if e.event_type == "cancellation"]
                    st.dataframe(pd.DataFrame([{
                        "Title": ev.item_title or f"Vinted Order {ev.order_id}",
                        "Price": f"${ev.sold_price:.2f}",
                        "Buyer": f"@{ev.buyer_username}" if ev.buyer_username else "-",
                        "Order ID": ev.order_id or "-",
                        "Date": ev.timestamp.strftime("%Y-%m-%d"),
                    } for ev in canc_evs]), width="stretch")

            # Default Inventory Lot Selector for Email Sales
            NO_LOT_OPTION = "None / No Lot (Unassigned)"
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
                st.markdown("##### Detected Sales (Editable Preview)")
                st.caption("You can edit the **Title**, choose an **Assigned Lot**, and verify **Brand** or **Category** before syncing to `Sold_Items`.")

                available_lot_ids = [l.get("Lot_ID", "") for l in all_lots if l.get("Lot_ID")]
                if target_email_lot and target_email_lot not in available_lot_ids:
                    available_lot_ids.append(target_email_lot)

                sale_rows = []
                for idx, ev in enumerate(preview_events):
                    if ev.event_type in ("sale", "bundle_sale"):
                        sale_indices.append(idx)
                        cur_lot = getattr(ev, "lot_id", "") or target_email_lot
                        cur_brand = getattr(ev, "brand", "") or (infer_brand(ev.item_title) if ev.item_title else "")
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

                for r in sale_rows:
                    if r["Assigned Lot"] and r["Assigned Lot"] not in available_lot_ids:
                        available_lot_ids.append(r["Assigned Lot"])
                lot_col_options = [""] + sorted(list(set(available_lot_ids)))

                cat_col_options = [
                    "Tops & Shirts", "Outerwear", "Pants & Jeans", "Sweaters & Hoodies",
                    "Dresses & Skirts", "Footwear", "Accessories", "Swimwear",
                    "Underwear & Loungewear", "Other Apparel"
                ]
                for r in sale_rows:
                    if r["Category"] and r["Category"] not in cat_col_options:
                        cat_col_options.append(r["Category"])

                sale_df = pd.DataFrame(sale_rows)
                edited_sale_df = st.data_editor(
                    sale_df,
                    column_config={
                        "Title": st.column_config.TextColumn("Title", required=True, width="large"),
                        "Assigned Lot": st.column_config.SelectboxColumn(
                            "Assigned Lot",
                            options=lot_col_options,
                            format_func=lambda x: "None (Unassigned)" if not x else x,
                            width="medium"
                        ) if available_lot_ids else st.column_config.TextColumn("Assigned Lot", width="medium"),
                        "Brand": st.column_config.TextColumn("Brand", width="medium"),
                        "Category": st.column_config.SelectboxColumn("Category", options=cat_col_options, width="medium"),
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
                with st.expander(f"View Other Detected Events ({len(non_sales)} offers & favorites)", icon=":material/forum:", expanded=False):
                    st.dataframe(pd.DataFrame([{
                        "Type": ev.event_type.upper(),
                        "Item": ev.item_title or ev.vinted_id,
                        "Detail": f"${ev.offer_price:.2f}" if ev.event_type == "offer" else "Favorited",
                        "User": f"@{ev.buyer_username}" if ev.buyer_username else "-",
                        "Date": ev.timestamp.strftime("%Y-%m-%d %H:%M"),
                    } for ev in non_sales]), width="stretch")

            action_c1, action_c2 = st.columns([1.5, 1])
            with action_c1:
                if st.button("Confirm & Sync to Google Sheets", icon=":material/cloud_upload:", type="primary", width="stretch", key="btn_confirm_email_sync"):
                    with st.spinner("Synchronizing email events to Google Sheets..."):
                        try:
                            # Apply any edits from st.data_editor to preview_events
                            if edited_sale_df is not None and not edited_sale_df.empty:
                                for s_idx, df_idx in enumerate(sale_indices):
                                    if s_idx < len(edited_sale_df):
                                        row = edited_sale_df.iloc[s_idx]
                                        ev = preview_events[df_idx]
                                        val_title = row.get("Title")
                                        ed_title = str(val_title).strip() if (val_title is not None and not pd.isna(val_title)) else ""
                                        if ed_title and ed_title != "None":
                                            ev.item_title = ed_title
                                        raw_lot = row.get("Assigned Lot")
                                        if pd.isna(raw_lot) or raw_lot is None:
                                            ev.lot_id = ""
                                        else:
                                            s_lot = str(raw_lot).strip()
                                            ev.lot_id = "" if s_lot in ("", "None", "None (Unassigned)") else s_lot
                                        raw_brand = row.get("Brand")
                                        ev.brand = str(raw_brand).strip() if (raw_brand is not None and not pd.isna(raw_brand) and str(raw_brand).strip() != "None") else ""
                                        raw_cat = row.get("Category")
                                        ev.category = str(raw_cat).strip() if (raw_cat is not None and not pd.isna(raw_cat) and str(raw_cat).strip() != "None") else ""

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
                            st.success(f"Successfully synced {processed} email transactions to your Google Sheet with full lot, brand, and category assignments!", icon=":material/check_circle:")
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            log_error(u_email, "sync.py:email_sync_confirm", e)
                            st.error(f"Sync failed: {e}")

            with action_c2:
                if st.button("Cancel / Clear Preview", icon=":material/close:", type="secondary", width="stretch", key="btn_cancel_email_sync"):
                    st.session_state.email_sync_preview = None
                    st.rerun()

    # ---------------------------------------------------------
    # TAB 3: DUPLICATE CHECKER & RESOLVER
    # ---------------------------------------------------------
    with tab_dups:
        st.markdown("### Duplicate Checker & Resolver")
        st.caption(
            "Scan your Google Sheet (`Sold_Items` or `Item_Listings`) for duplicate records caused by multiple imports, "
            "email notifications, or placeholder rows. Compare matching items side-by-side and choose whether to keep one, "
            "merge missing fields, or keep both."
        )

        dup_engine = get_sheets_sync_engine(client)

        col_cfg1, col_cfg2 = st.columns([2, 1], vertical_alignment="bottom")
        with col_cfg1:
            sheet_options = {
                "Sold Items (Sold_Items)": "Sold_Items",
                "Active Listings (Item_Listings)": "Item_Listings",
            }
            selected_sheet_label = st.segmented_control(
                "Select sheet to inspect:",
                options=list(sheet_options.keys()),
                default="Sold Items (Sold_Items)",
                key="dup_target_sheet_choice",
            ) or "Sold Items (Sold_Items)"
            target_tab_name = sheet_options.get(selected_sheet_label, "Sold_Items")

        with col_cfg2:
            auto_scan = st.session_state.pop("duplicate_scan_trigger", False)
            btn_scan = st.button(
                "Scan for Duplicates",
                type="primary",
                width="stretch",
                icon=":material/search:",
                key="btn_run_duplicate_scan",
            )

        if btn_scan or auto_scan:
            with st.spinner(f"Analyzing {selected_sheet_label} for duplicate entries, matching Order IDs, and placeholder rows..."):
                try:
                    dup_groups = dup_engine.find_duplicate_groups(tab_name=target_tab_name)
                    st.session_state.duplicate_scan_results = {
                        "tab_name": target_tab_name,
                        "sheet_label": selected_sheet_label,
                        "groups": dup_groups,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    # Initialize default decisions for each group
                    for g in dup_groups:
                        rec_idx = g.get("recommended_keep_row_idx")
                        g_id = g["group_id"]
                        st.session_state[f"dup_decision_{target_tab_name}_{g_id}"] = f"merge_into:{rec_idx}"
                except Exception as e:
                    log_error(u_email, "sync.py:find_duplicate_groups", e)
                    st.error(f"Error scanning for duplicates: {e}")

        scan_data = st.session_state.get("duplicate_scan_results")

        if scan_data and scan_data.get("tab_name") == target_tab_name:
            groups = scan_data.get("groups", [])
            if not groups:
                st.success(
                    f"**No duplicates found in `{target_tab_name}`!** Every row has a unique Order ID, URL, or item details.",
                    icon=":material/check_circle:",
                )
            else:
                total_dup_rows = sum(len(g.get("rows", [])) for g in groups)
                st.warning(
                    f"Found **{len(groups)} duplicate group(s)** involving **{total_dup_rows} total rows** in `{target_tab_name}`. "
                    f"Review the side-by-side details below and select your decision for each group.",
                    icon=":material/warning:",
                )

                # Bulk selection helpers
                st.markdown("##### Quick Actions for All Groups")
                b_c1, b_c2, b_c3 = st.columns(3)
                with b_c1:
                    if st.button("Merge into Best for All", width="stretch", icon=":material/auto_fix_high:", key="btn_bulk_merge"):
                        for g in groups:
                            rec_idx = g.get("recommended_keep_row_idx")
                            st.session_state[f"dup_decision_{target_tab_name}_{g['group_id']}"] = f"merge_into:{rec_idx}"
                        st.rerun()
                with b_c2:
                    if st.button("Keep Best Row Only for All", width="stretch", icon=":material/delete_forever:", key="btn_bulk_keep_best"):
                        for g in groups:
                            rec_idx = g.get("recommended_keep_row_idx")
                            st.session_state[f"dup_decision_{target_tab_name}_{g['group_id']}"] = f"keep_row:{rec_idx}"
                        st.rerun()
                with b_c3:
                    if st.button("Keep Both for All", width="stretch", icon=":material/people:", key="btn_bulk_keep_both"):
                        for g in groups:
                            st.session_state[f"dup_decision_{target_tab_name}_{g['group_id']}"] = "keep_both"
                        st.rerun()

                st.divider()

                # Render each duplicate group
                try:
                    for g_idx, g in enumerate(groups):
                        g_id = g["group_id"]
                        rec_idx = g.get("recommended_keep_row_idx")
                        rows = g.get("rows", [])

                        with st.container(border=True):
                            # Group Header
                            g_h1, g_h2 = st.columns([3, 1])
                            with g_h1:
                                st.markdown(f"#### Group #{g_idx + 1}")
                                st.caption(f"**Detection Reason:** {g.get('reason', 'Duplicate item match')}")
                            with g_h2:
                                st.metric("Duplicate Rows", len(rows), border=False)

                            # Side-by-side comparison of items
                            row_cols = st.columns(len(rows))
                            for r_col_idx, (r_col, row_info) in enumerate(zip(row_cols, rows)):
                                r_idx = row_info.get("row_idx")
                                is_rec = (r_idx == rec_idx)
                                with r_col:
                                    card_border = "2px solid #22c55e" if is_rec else "1px solid #334155"
                                    bg_color = "rgba(34, 197, 94, 0.08)" if is_rec else "rgba(30, 41, 59, 0.5)"
                                    badge_html = (
                                        '<span style="background-color:#166534;color:#bbf7d0;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700;display:inline-flex;align-items:center;gap:4px;"><span class="material-symbols-rounded" style="font-size:13px;line-height:1;">verified</span> RECOMMENDED (MOST COMPLETE)</span>'
                                        if is_rec else
                                        '<span style="background-color:#334155;color:#94a3b8;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">DUPLICATE CANDIDATE</span>'
                                    )

                                    price_val = row_info.get("price", 0.0)
                                    price_display = f"${price_val:.2f}" if price_val > 0 else "—"
                                    order_display = row_info.get("order_id") or "—"
                                    sku_display = row_info.get("sku") or "—"
                                    lot_display = row_info.get("lot_id") or row_info.get("lot") or "—"
                                    brand_display = row_info.get("brand") or "—"
                                    cat_display = row_info.get("category") or "—"
                                    date_display = row_info.get("date") or "—"
                                    title_display = row_info.get("title") or "*(No Title)*"
                                    comp_score = row_info.get("completeness_score", 0)

                                    source_val = row_info.get("source") or "—"
                                    if "Email" in source_val and "HTML" in source_val:
                                        source_badge = '<span style="background-color:#4338ca;color:#c7d2fe;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;">Email + HTML</span>'
                                    elif "Email" in source_val:
                                        source_badge = '<span style="background-color:#0369a1;color:#bae6fd;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;">Email</span>'
                                    elif "HTML" in source_val:
                                        source_badge = '<span style="background-color:#0f766e;color:#99f6e4;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;">HTML</span>'
                                    else:
                                        source_badge = f'<span style="background-color:#334155;color:#94a3b8;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;">{html.escape(source_val)}</span>'

                                    st.markdown(
                                        f"""
                                        <div style="border:{card_border};background:{bg_color};border-radius:8px;padding:12px;margin-bottom:8px;">
                                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                                                <span style="font-weight:700;font-size:14px;color:#f8fafc;">Row #{r_idx}</span>
                                                {badge_html}
                                            </div>
                                            <div style="font-size:13px;font-weight:600;color:#e2e8f0;margin-bottom:6px;">{html.escape(title_display)}</div>
                                            <div style="font-size:12px;color:#94a3b8;line-height:1.5;">
                                                <div><strong>Price:</strong> <span style="color:#38bdf8;">{price_display}</span> &nbsp;|&nbsp; <strong>Date:</strong> {date_display}</div>
                                                <div><strong>Brand:</strong> {html.escape(brand_display)} &nbsp;|&nbsp; <strong>Cat:</strong> {html.escape(cat_display)}</div>
                                                <div><strong>Lot:</strong> {html.escape(lot_display)} &nbsp;|&nbsp; <strong>SKU:</strong> {html.escape(sku_display)}</div>
                                                <div><strong>Order ID:</strong> <code>{html.escape(order_display)}</code> &nbsp;|&nbsp; <strong>Source:</strong> {source_badge}</div>
                                                <div><strong>Completeness Score:</strong> {comp_score}/8</div>
                                            </div>
                                        </div>
                                        """,
                                        unsafe_allow_html=True,
                                    )

                            # Decision Radio / Options for this group
                            choice_options = {}
                            choice_options[f"merge_into:{rec_idx}"] = f"Merge missing fields into Row #{rec_idx} & delete other row(s) (Recommended)"
                            for row_info in rows:
                                r_i = row_info.get("row_idx")
                                other_indices = [r.get("row_idx") for r in rows if r.get("row_idx") != r_i]
                                others_str = ", ".join(f"#{x}" for x in other_indices)
                                choice_options[f"keep_row:{r_i}"] = f"Keep Row #{r_i} only (delete {others_str})"
                            choice_options["keep_both"] = "Keep Both rows (do not delete or change anything)"

                            group_key = f"dup_decision_{target_tab_name}_{g_id}"
                            if group_key not in st.session_state or st.session_state[group_key] not in choice_options:
                                st.session_state[group_key] = f"merge_into:{rec_idx}"

                            chosen_key = st.radio(
                                f"Decision for Group #{g_idx + 1}:",
                                options=list(choice_options.keys()),
                                format_func=lambda k: choice_options[k],
                                index=list(choice_options.keys()).index(st.session_state[group_key]),
                                key=group_key,
                            )
                except Exception as e:
                    log_error(u_email, "sync.py:render_duplicate_groups", e)
                    st.error(f"Error displaying duplicate groups: {e}")

                st.divider()

                # Action buttons
                act_c1, act_c2 = st.columns([2, 1])
                with act_c1:
                    if st.button(
                        f"Apply Decisions & Resolve Duplicates in {target_tab_name}",
                        type="primary",
                        width="stretch",
                        icon=":material/check:",
                        key="btn_apply_duplicate_resolutions",
                    ):
                        with st.spinner(f"Applying resolutions, merging records, and cleaning {target_tab_name}..."):
                            decisions_payload = []
                            for g in groups:
                                g_id = g["group_id"]
                                group_key = f"dup_decision_{target_tab_name}_{g_id}"
                                dec_val = st.session_state.get(group_key, f"merge_into:{g.get('recommended_keep_row_idx')}")
                                all_indices = [r.get("row_idx") for r in g.get("rows", [])]

                                if not dec_val or dec_val == "keep_both":
                                    decisions_payload.append({
                                        "group_id": g_id,
                                        "action": "keep_both",
                                        "keep_row_idx": None,
                                        "discard_row_indices": [],
                                    })
                                elif str(dec_val).startswith("merge_into:"):
                                    k_idx = int(dec_val.split(":")[1])
                                    d_indices = [idx for idx in all_indices if idx != k_idx]
                                    decisions_payload.append({
                                        "group_id": g_id,
                                        "action": "merge_into",
                                        "keep_row_idx": k_idx,
                                        "discard_row_indices": d_indices,
                                    })
                                elif str(dec_val).startswith("keep_row:"):
                                    k_idx = int(dec_val.split(":")[1])
                                    d_indices = [idx for idx in all_indices if idx != k_idx]
                                    decisions_payload.append({
                                        "group_id": g_id,
                                        "action": "keep_row",
                                        "keep_row_idx": k_idx,
                                        "discard_row_indices": d_indices,
                                    })

                            try:
                                res = dup_engine.resolve_duplicates(
                                    tab_name=target_tab_name,
                                    decisions=decisions_payload,
                                )
                                log_event(
                                    u_email,
                                    "duplicate_resolution",
                                    f"Resolved {len(decisions_payload)} groups: deleted {res['deleted_count']} rows, merged {res['merged_count']} field sets, kept both for {res['kept_both_count']}",
                                    spreadsheet_id=st.session_state.get("spreadsheet_id"),
                                )
                                st.cache_data.clear()
                                st.session_state.duplicate_scan_results = None
                                st.success(
                                    f"Successfully resolved duplicates! Deleted {res['deleted_count']} redundant row(s), "
                                    f"merged missing details for {res['merged_count']} item(s), and preserved {res['kept_both_count']} group(s) intact.",
                                    icon=":material/task_alt:",
                                )
                                st.balloons()
                                time.sleep(1.5)
                                st.rerun()
                            except Exception as e:
                                log_error(u_email, "sync.py:resolve_duplicates", e)
                                st.error(f"Error applying duplicate resolutions: {e}")

                with act_c2:
                    if st.button("Clear Results / Cancel", icon=":material/close:", type="secondary", width="stretch", key="btn_cancel_dup_scan"):
                        st.session_state.duplicate_scan_results = None
                        st.rerun()

    # ---------------------------------------------------------
    # TAB 4: SHIPPING SUPPLIES
    # ---------------------------------------------------------
    with tab_supplies:
        st.markdown("### Shipping Supplies & Packaging Costs")
        st.caption("Manage boxes, poly mailers, labels, and tape. The global weighted average packaging cost is automatically deducted from every sale in `Sold_Items` to compute true net profits.")

        sup_engine = get_sheets_sync_engine(client)
        try:
            sup_data = get_cached_supplies(sheet_id, user_creds)
        except Exception as e:
            if "429" in str(e):
                st.warning("Google Sheets rate limit reached. Waiting for quota reset...", icon=":material/hourglass_top:")
                st.button("Retry Supplies", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), key="retry_sup_btn")
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
            st.markdown("#### Add or Update Shipping Supply")

            next_sup_id = sup_engine.get_next_supply_id(cached_supplies=active_supplies)
            sup_mode_options = [f"New Supply ({next_sup_id})"] + [
                f"{s['Supply_ID']} — {s.get('Item_Description') or 'Supply'} ({s.get('Vendor') or 'Vendor'})"
                for s in active_supplies
            ]

            selected_sup_mode = st.selectbox(
                "Action / Select Supply to Edit",
                options=sup_mode_options,
                key="select_supply_mode",
            )

            is_editing_existing = not selected_sup_mode.startswith("New Supply")
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
            st.info(f"Calculated Unit Packaging Cost: **${calc_sup_unit:.3f} per unit** (`${input_sup_cost:.2f} ÷ {input_sup_qty} units`)", icon=":material/lightbulb:")

            if st.button("Save Supply to Spreadsheet", icon=":material/save:", type="primary", width="stretch", key="btn_save_supply"):
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

                st.success(f"Supply {form_sup_id} saved successfully! Global packaging deduction updated in Google Sheets.", icon=":material/check_circle:")
                st.balloons()
                time.sleep(1.2)
                st.rerun()

        # Section 2: Current Inventory Table
        st.markdown("#### Current Shipping Supplies in Sheet")
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
            st.info("No shipping supplies recorded yet.", icon=":material/info:")


if __name__ == "__main__":
    render_sync_page()
else:
    render_sync_page()
