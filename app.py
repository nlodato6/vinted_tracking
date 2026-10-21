"""Streamlit Multi-Tenant Web App for Vinted Inventory & Sales Tracking."""

import os
import io
import time
from typing import List, Dict, Any, Optional
import streamlit as st
import pandas as pd

from src.config import settings
from src.sheets import GoogleSheetsClient, SheetsSyncEngine
from src.html_parser import parse_closet_html, parse_orders_html, detect_html_type
from src.email_parser import fetch_vinted_emails_via_imap
from src.notifications import record_html_parse_event, record_email_parse_event, get_sync_status
from src.auth import is_oauth_configured, get_authorization_url, exchange_code_for_credentials

# Page Configuration
st.set_page_config(
    page_title="Vinted Sales & Inventory Tracker",
    page_icon="👕",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Premium Styling
st.markdown("""
<style>
    /* Metric Cards */
    .metric-card {
        background: linear-gradient(135deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 12px;
        padding: 18px 22px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        margin-bottom: 12px;
    }
    .metric-title {
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #94a3b8;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: #0ea5e9;
    }
    /* Status Badge */
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 9999px;
        font-size: 0.78rem;
        font-weight: 600;
        background-color: #dcfce7;
        color: #166534;
        border: 1px solid #bbf7d0;
    }
    .status-badge-pending {
        background-color: #fef9c3;
        color: #854d0e;
        border: 1px solid #fef08a;
    }
</style>
""", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Session State Initialization
# -----------------------------------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_info" not in st.session_state:
    st.session_state.user_info = {}
if "user_credentials" not in st.session_state:
    st.session_state.user_credentials = None
if "spreadsheet_id" not in st.session_state:
    st.session_state.spreadsheet_id = settings.google_sheet_id
if "selected_lot" not in st.session_state:
    st.session_state.selected_lot = "LOT-001"
if "parsed_results" not in st.session_state:
    st.session_state.parsed_results = None


def get_sheets_client() -> GoogleSheetsClient:
    """Returns a GoogleSheetsClient configured for the current session user."""
    return GoogleSheetsClient(
        sheet_id=st.session_state.spreadsheet_id,
        credentials=st.session_state.user_credentials,
    )


# -----------------------------------------------------------------------------
# OAuth Callback Handler
# -----------------------------------------------------------------------------
query_params = st.query_params
if "code" in query_params and not st.session_state.authenticated:
    code = query_params["code"]
    state = query_params.get("state")
    try:
        redirect_uri = "http://localhost:8501"
        creds, user_info = exchange_code_for_credentials(code, redirect_uri=redirect_uri, state=state)
        st.session_state.user_credentials = creds
        st.session_state.user_info = user_info
        st.session_state.authenticated = True
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Authentication failed: {e}")


# -----------------------------------------------------------------------------
# Authentication Screen (if not logged in)
# -----------------------------------------------------------------------------
if not st.session_state.authenticated:
    st.title("👕 Vinted Sales & Inventory Tracker")
    st.markdown("Automate your Vinted inventory tracking, net profits, and price drop history directly in Google Sheets.")

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.markdown("### Welcome! Choose How to Sign In")
        
        # 1. Sign in with Google (OAuth)
        if is_oauth_configured():
            try:
                auth_url, _ = get_authorization_url(redirect_uri="http://localhost:8501")
                st.link_button("🔑 Sign in with Google", auth_url, type="primary", use_container_width=True)
                st.caption("Sign in with your Google account to connect directly to your personal Google Sheets.")
            except Exception as e:
                st.warning(f"OAuth configuration notice: {e}")
        else:
            st.info("ℹ️ Google OAuth can be enabled by setting `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` or Streamlit Secrets.")

        st.divider()

        # 2. Local / Development Direct Access
        st.markdown("#### Local Mode (Personal Workspace)")
        sa_exists = settings.google_service_account_file.exists()
        
        if sa_exists:
            if st.button("🚀 Continue with Local Credentials", type="secondary", use_container_width=True):
                st.session_state.authenticated = True
                st.session_state.user_info = {
                    "email": "local-admin@vinted-tracker",
                    "name": "Local Admin",
                }
                st.rerun()
            st.caption(f"Uses local Service Account: `{settings.google_service_account_file.name}`")
        else:
            st.warning("No local `credentials.json` found. Please sign in with Google or place credentials.json in your project directory.")

    with col2:
        st.markdown("""
        ### What This App Does
        * **1-Click HTML Import**: Upload your saved Vinted profile or orders HTML to update active items and sales.
        * **Zero Duplicate Guarantee**: Intelligent set-based reconciliation avoids duplicating previously imported items.
        * **Real-Time Email Sync**: Pull sales and offers directly from `tsavoie90@gmail.com` via IMAP.
        * **100% Free**: Built on standard Google APIs with zero bot scraping headaches.
        """)

    st.stop()


# -----------------------------------------------------------------------------
# Sidebar: Settings & User Profile
# -----------------------------------------------------------------------------
with st.sidebar:
    u_email = st.session_state.user_info.get("email", "Local User")
    u_name = st.session_state.user_info.get("name", "Vinted Seller")
    u_pic = st.session_state.user_info.get("picture")

    if u_pic:
        st.sidebar.image(u_pic, width=54)
    st.sidebar.markdown(f"**{u_name}**")
    st.sidebar.caption(u_email)

    if st.sidebar.button("Log Out"):
        st.session_state.authenticated = False
        st.session_state.user_credentials = None
        st.session_state.user_info = {}
        st.rerun()

    st.sidebar.divider()
    st.sidebar.markdown("### 📊 Google Sheet")

    # Template Clone Button
    master_template_id = "1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg"
    template_copy_url = f"https://docs.google.com/spreadsheets/d/{master_template_id}/copy"
    st.sidebar.link_button("📋 Copy Sheet Template", template_copy_url, use_container_width=True)

    sheet_id_input = st.sidebar.text_input(
        "Spreadsheet ID or URL",
        value=st.session_state.spreadsheet_id,
        help="Paste the ID from your Google Sheet URL (https://docs.google.com/spreadsheets/d/<ID>/edit)",
    )
    if sheet_id_input:
        import re
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_id_input)
        clean_id = m.group(1) if m else sheet_id_input.strip()
        if clean_id != st.session_state.spreadsheet_id:
            st.session_state.spreadsheet_id = clean_id
            st.rerun()

    if st.session_state.spreadsheet_id:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{st.session_state.spreadsheet_id}/edit"
        st.sidebar.link_button("🔗 Open Live Sheet", sheet_url, use_container_width=True)

    st.sidebar.divider()
    st.sidebar.markdown("### 🏷️ Inventory Lot")
    lot_input = st.sidebar.text_input("Default Lot ID", value=st.session_state.selected_lot)
    if lot_input:
        st.session_state.selected_lot = lot_input.strip()


# -----------------------------------------------------------------------------
# Main Dashboard Header & Metrics
# -----------------------------------------------------------------------------
st.title("👕 Vinted Sales & Inventory Hub")

# Fetch live sheet status
client = get_sheets_client()
status_state = get_sync_status()

try:
    sh = client.get_spreadsheet()
    ws_l = sh.worksheet("Item_Listings")
    ws_s = sh.worksheet("Sold_Items")
    active_cnt = max(0, len(ws_l.get_all_values()) - 1)
    sold_cnt = max(0, len(ws_s.get_all_values()) - 1)
    connected = True
except Exception as e:
    active_cnt = 0
    sold_cnt = 0
    connected = False

# KPI Metric Row
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Active Listings</div>
        <div class="metric-value">{active_cnt}</div>
    </div>
    """, unsafe_allow_html=True)
with m2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Sold Items</div>
        <div class="metric-value">{sold_cnt}</div>
    </div>
    """, unsafe_allow_html=True)
with m3:
    h_time = status_state.get("last_html_parsed_at", "Not yet run")
    badge_cls = "status-badge" if h_time != "Not yet run" else "status-badge status-badge-pending"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Last HTML Import</div>
        <div style="font-size: 1.1rem; font-weight: 600; color: #f8fafc; margin-top: 4px;">{h_time}</div>
        <span class="{badge_cls}">{'Recorded' if h_time != 'Not yet run' else 'Pending'}</span>
    </div>
    """, unsafe_allow_html=True)
with m4:
    e_time = status_state.get("last_email_parsed_at", "Not yet run")
    badge_cls = "status-badge" if e_time != "Not yet run" else "status-badge status-badge-pending"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Last Email Parsed</div>
        <div style="font-size: 1.1rem; font-weight: 600; color: #f8fafc; margin-top: 4px;">{e_time}</div>
        <span class="{badge_cls}">{'Active' if e_time != 'Not yet run' else 'Pending'}</span>
    </div>
    """, unsafe_allow_html=True)

if not connected:
    st.warning("⚠️ Could not connect to Google Spreadsheet. Check permissions or ensure your spreadsheet is shared with your account.")


# -----------------------------------------------------------------------------
# Main Tabs: HTML Import & Email Sync
# -----------------------------------------------------------------------------
tab_html, tab_email = st.tabs(["📦 HTML Batch Import & Reconciliation", "📬 Direct Email Sync"])

# ---------------------------------------------------------
# TAB 1: HTML IMPORT
# ---------------------------------------------------------
with tab_html:
    st.markdown("### Upload Saved Vinted HTML Files")
    st.caption("Drag and drop your saved HTML files (`Active Listings`, `Sold Listings`, `My Orders`). You can upload all 3 files at once.")

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
                items = parse_closet_html(file_bytes, default_lot=st.session_state.selected_lot)
                parsed_closet.extend(items)
                type_label = "📦 Active Listings" if detected == "active_closet" else "🏷️ Sold Closet"
                files_info.append({"Filename": f.name, "Detected Type": type_label, "Items Found": len(items)})
            else:
                files_info.append({"Filename": f.name, "Detected Type": "❓ Unknown", "Items Found": 0})

        st.dataframe(pd.DataFrame(files_info), use_container_width=True)

        # Reconciliation Preview
        if parsed_orders or parsed_closet:
            st.divider()
            st.markdown("#### ⚡ Reconciliation Preview (Zero-Duplication)")
            
            # Fetch current sheet state for in-memory comparison
            try:
                engine = SheetsSyncEngine(client)
                ws_list = sh.worksheet("Item_Listings")
                ws_sold = sh.worksheet("Sold_Items")
                curr_active_titles = {str(r[2]).strip().lower(): r for r in ws_list.get_all_values()[1:] if len(r) > 2}
                curr_sold_titles = {str(r[2]).strip().lower(): r for r in ws_sold.get_all_values()[1:] if len(r) > 2}

                new_listings = [it for it in parsed_closet if it.status != "Sold" and it.title.strip().lower() not in curr_active_titles and it.title.strip().lower() not in curr_sold_titles]
                sold_transitions = [it for it in parsed_orders if it.item_title.strip().lower() in curr_active_titles]
                already_sold = [it for it in parsed_orders if it.item_title.strip().lower() in curr_sold_titles]

                p1, p2, p3 = st.columns(3)
                p1.metric("🟢 New Active to Add", len(new_listings))
                p2.metric("🔵 Active Moving to Sold", len(sold_transitions))
                p3.metric("⚪ Duplicates Skipped", len(already_sold) + (len(parsed_closet) - len(new_listings)))

                if sold_transitions:
                    with st.expander("View Items Moving to Sold", expanded=False):
                        st.dataframe(pd.DataFrame([{"Title": o.item_title, "Price": f"${o.sold_price:.2f}", "Order ID": o.order_id} for o in sold_transitions]))

                if new_listings:
                    with st.expander("View New Listings to Add", expanded=False):
                        st.dataframe(pd.DataFrame([{"Title": i.title, "Brand": i.brand, "Price": f"${i.posted_price:.2f}", "Category": i.category} for i in new_listings]))

                if st.button("🚀 Commit & Synchronize to Google Sheets", type="primary", use_container_width=True):
                    with st.spinner("Synchronizing to Google Sheets..."):
                        res_orders = engine.sync_orders(parsed_orders) if parsed_orders else {"synced_orders": 0}
                        res_closet = engine.sync_listings(parsed_closet) if parsed_closet else {"updated": 0, "created": 0}
                        engine.sync_lots()
                        record_html_parse_event(details=f"Web: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items)", client=client)

                    st.success(f"✔ Sync Complete! {res_orders.get('synced_orders', 0)} orders synced, {res_closet.get('created', 0)} listings added, {res_closet.get('updated', 0)} listings refreshed.")
                    st.balloons()
                    time.sleep(1.5)
                    st.rerun()

            except Exception as e:
                st.error(f"Error during reconciliation preview: {e}")


# ---------------------------------------------------------
# TAB 2: EMAIL SYNC
# ---------------------------------------------------------
with tab_email:
    st.markdown("### 📬 Direct Gmail Sync (`tsavoie90@gmail.com`)")
    st.caption("Pull incoming sales, buyer offers, and favorites directly from Gmail via secure IMAP.")

    e1, e2 = st.columns([1, 1])

    with e1:
        gmail_user_input = st.text_input("Gmail Address", value=os.getenv("GMAIL_USER", "tsavoie90@gmail.com"))
        gmail_pass_input = st.text_input("Google App Password (16 characters)", type="password", value=os.getenv("GMAIL_APP_PASSWORD", ""), help="16-character passcode generated at myaccount.google.com/apppasswords")
        max_emails = st.slider("Max emails to scan", min_value=10, max_value=100, value=25)

        if st.button("🔄 Sync Vinted Emails Now", type="primary", use_container_width=True):
            if not gmail_user_input or not gmail_pass_input:
                st.error("Please provide your Gmail address and 16-character App Password.")
            else:
                with st.spinner(f"Connecting to {gmail_user_input} via IMAP..."):
                    try:
                        events = fetch_vinted_emails_via_imap(
                            username=gmail_user_input.strip(),
                            password=gmail_pass_input.strip(),
                            max_emails=max_emails,
                        )
                        if not events:
                            st.info("No new Vinted transactional emails found in inbox.")
                        else:
                            st.success(f"Found {len(events)} Vinted notification emails! Updating sheet...")
                            engine = SheetsSyncEngine(client)
                            processed = 0
                            event_rows = []
                            for ev in events:
                                res = engine.process_email_event(ev)
                                processed += 1
                                event_rows.append({
                                    "Type": ev.event_type.upper(),
                                    "Title / Ref": ev.item_title or ev.order_id,
                                    "Status": res.get("status", "processed"),
                                    "Timestamp": ev.timestamp.strftime("%Y-%m-%d %H:%M"),
                                })
                            engine.sync_lots()
                            record_email_parse_event(details=f"Web IMAP: {processed} events from {gmail_user_input}", client=client)
                            st.dataframe(pd.DataFrame(event_rows), use_container_width=True)
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                    except Exception as e:
                        st.error(f"IMAP Sync failed: {e}")

    with e2:
        st.markdown("""
        #### Need an App Password?
        Google requires a 16-character **App Password** for direct IMAP access:
        1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords) (while logged into `tsavoie90@gmail.com`).
        2. Enter app name: `Vinted Tracker` $\rightarrow$ click **Create**.
        3. Copy the 16-character password into the box on the left.
        4. Reference the guide: [`GOOGLE_APP_PASSWORD_SETUP.md`](./GOOGLE_APP_PASSWORD_SETUP.md).
        """)
