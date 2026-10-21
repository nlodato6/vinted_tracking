"""Sync & Batch Import page for Vinted sales and inventory tracking."""

import os
import time
from datetime import datetime, date
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
        active_cnt = max(0, len(active_rows) - 1)
        sold_cnt = max(0, len(sold_rows) - 1)
        return active_cnt, sold_cnt
    except Exception:
        ws_l = sh.worksheet("Item_Listings")
        ws_s = sh.worksheet("Sold_Items")
        return max(0, len(ws_l.get_all_values()) - 1), max(0, len(ws_s.get_all_values()) - 1)


@st.cache_data(ttl=60)
def get_cached_lots(sheet_id: str, _creds):
    """Fetches all lots with 60-second cache."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    engine = SheetsSyncEngine(client)
    return engine.get_all_lots()


@st.cache_data(ttl=60)
def get_cached_supplies(sheet_id: str, _creds):
    """Fetches all shipping supplies with 60-second cache."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    engine = SheetsSyncEngine(client)
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


def render_sync_page():
    st.header("⚡ SpreadThrift Operations & Sync Hub")
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
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Active Listings", active_cnt, border=True)
    with m2:
        st.metric("Sold Items", sold_cnt, border=True)
    with m3:
        h_time = status_state.get("last_html_parsed_at", "Not yet run")
        st.metric("Last HTML Import", h_time, border=True)
    with m4:
        e_time = status_state.get("last_email_parsed_at", "Not yet run")
        st.metric("Last Email Parsed", e_time, border=True)

    if not connected:
        st.warning("⚠️ Could not connect to Google Spreadsheet. Check permissions or ensure your spreadsheet is shared with your account.")
        return

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
        engine = SheetsSyncEngine(client)
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
            st.caption("Choose which inventory lot new items should be assigned to, or add a brand new lot to your spreadsheet.")

            lot_options = []
            lot_id_map = {}

            for l in all_lots:
                lid = l.get("Lot_ID", "")
                vendor = l.get("Source_Vendor", "Vendor")
                cost = l.get("Unit_Cost", "$0.00")
                label = f"{lid} — {vendor} ({cost}/ea)"
                lot_options.append(label)
                lot_id_map[label] = lid

            lot_options.append("➕ Add New Lot...")

            # Determine default index based on current session lot
            default_idx = 0
            cur_selected = st.session_state.get("selected_lot", "LOT-001")
            for idx, opt in enumerate(lot_options):
                if opt.startswith(cur_selected):
                    default_idx = idx
                    break

            selected_option = st.selectbox(
                "Select Inventory Lot for New Listings:",
                options=lot_options,
                index=default_idx,
                help="Select an existing lot from your sheet or choose '➕ Add New Lot...' to register a new haul.",
                key="sync_lot_selectbox",
            )

            is_new_lot = (selected_option == "➕ Add New Lot...")

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
                        st.session_state["sync_lot_selectbox"] = lot_options[0]

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
            else:
                target_lot_id = lot_id_map.get(selected_option, st.session_state.get("selected_lot", "LOT-001"))
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
        st.markdown("### Upload Saved Vinted HTML Files")
        st.caption(f"Drag and drop your saved HTML files (`Active Listings`, `Sold Listings`, `My Orders`). Newly detected items will be assigned to **{target_lot_id}**.")

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
            # 3. Reconciliation Preview (Zero-Duplication)
            # -------------------------------------------------------------
            if parsed_orders or parsed_closet:
                st.divider()
                st.markdown(f"#### ⚡ Reconciliation Preview (Assigned to {target_lot_id})")
                
                try:
                    curr_active_titles, curr_sold_titles = get_cached_titles(sheet_id, user_creds)

                    new_listings = [it for it in parsed_closet if it.status != "Sold" and it.title.strip().lower() not in curr_active_titles and it.title.strip().lower() not in curr_sold_titles]
                    sold_transitions = [it for it in parsed_orders if it.item_title.strip().lower() in curr_active_titles]
                    already_sold = [it for it in parsed_orders if it.item_title.strip().lower() in curr_sold_titles]

                    # Assign target lot ID to new items in preview
                    for it in new_listings:
                        it.lot_id = target_lot_id

                    p1, p2, p3 = st.columns(3)
                    p1.metric("🟢 New Active to Add", len(new_listings), f"Lot: {target_lot_id}", border=True)
                    p2.metric("🔵 Active Moving to Sold", len(sold_transitions), border=True)
                    p3.metric("⚪ Duplicates Skipped", len(already_sold) + (len(parsed_closet) - len(new_listings)), border=True)

                    if sold_transitions:
                        with st.expander("View Items Moving to Sold", expanded=False):
                            st.dataframe(pd.DataFrame([{"Title": o.item_title, "Price": f"${o.sold_price:.2f}", "Order ID": o.order_id} for o in sold_transitions]), width="stretch")

                    if new_listings:
                        with st.expander(f"View New Listings to Add (Assigned to {target_lot_id})", expanded=True):
                            st.dataframe(pd.DataFrame([{
                                "Title": i.title, 
                                "Brand": i.brand, 
                                "Price": f"${i.posted_price:.2f}", 
                                "Category": i.category,
                                "Assigned Lot": i.lot_id,
                            } for i in new_listings]), width="stretch")

                    if st.button("🚀 Commit & Synchronize to Google Sheets", type="primary", width="stretch"):
                        try:
                            with st.spinner("Synchronizing to Google Sheets..."):
                                # 1. If new lot, create it in Inventory_Lots if not already created
                                if is_new_lot:
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

                                # 2. Assign target lot to all parsed closet items
                                for it in parsed_closet:
                                    if not it.lot_id or is_new_lot or it in new_listings:
                                        it.lot_id = target_lot_id

                                # 3. Synchronize listings and orders
                                res_orders = engine.sync_orders(parsed_orders, default_lot=target_lot_id) if parsed_orders else {"synced_orders": 0}
                                res_closet = engine.sync_listings(parsed_closet) if parsed_closet else {"updated": 0, "created": 0}

                                # 4. Record event and clear cache
                                record_html_parse_event(details=f"Web: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items, Lot: {target_lot_id})", client=client)
                                log_event(u_email, "html_import", f"HTML import: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items, Lot: {target_lot_id})", spreadsheet_id=st.session_state.get("spreadsheet_id"))
                                st.cache_data.clear()

                            lot_msg = f" (New lot {target_lot_id} created in Inventory_Lots)" if is_new_lot else f" (Assigned to {target_lot_id})"
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
        st.markdown("### 📬 Direct Gmail Sync (`tsavoie90@gmail.com`)")
        st.caption("Pull incoming sales, buyer offers, and favorites directly from Gmail via secure IMAP.")

        e1, e2 = st.columns([1, 1])

        with e1:
            saved_u, saved_p = get_user_email_credentials(u_email)
            default_user = saved_u or os.getenv("GMAIL_USER", "tsavoie90@gmail.com")
            default_pwd = saved_p or os.getenv("GMAIL_APP_PASSWORD", "")

            gmail_user_input = st.text_input("Gmail Address", value=default_user)
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
            st.markdown("""
            #### Need an App Password?
            Google requires a 16-character **App Password** for direct IMAP access:
            1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords) (while logged into `tsavoie90@gmail.com`).
            2. Enter app name: `Vinted Tracker` $\rightarrow$ click **Create**.
            3. Copy the 16-character password into the box on the left.
            4. Reference the guide: [`GOOGLE_APP_PASSWORD_SETUP.md`](./GOOGLE_APP_PASSWORD_SETUP.md).
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

            preview_table_data = []
            for ev in preview_events:
                price_str = f"${ev.sold_price:.2f}" if ev.event_type == "sale" else (f"${ev.offer_price:.2f}" if ev.event_type == "offer" else "-")
                preview_table_data.append({
                    "Type": ev.event_type.upper(),
                    "Item / Reference": ev.item_title or ev.order_id,
                    "Price": price_str,
                    "Buyer": f"@{ev.buyer_username}" if ev.buyer_username else "-",
                    "Order ID": ev.order_id or "-",
                    "Date": ev.timestamp.strftime("%Y-%m-%d %H:%M"),
                })

            st.dataframe(pd.DataFrame(preview_table_data), width="stretch")

            action_c1, action_c2 = st.columns([1.5, 1])
            with action_c1:
                if st.button("🚀 Confirm & Sync to Google Sheets", type="primary", width="stretch", key="btn_confirm_email_sync"):
                    with st.spinner("Synchronizing email events to Google Sheets..."):
                        try:
                            engine = SheetsSyncEngine(client)
                            results = engine.process_email_batch(preview_events)
                            processed = len([r for r in results if r.get("status") not in ("ignored", "no_listings")])
                            log_event(
                                u_email,
                                "email_sync",
                                f"IMAP sync: {processed} events from {st.session_state.get('email_sync_user', gmail_user_input)}",
                                spreadsheet_id=st.session_state.get("spreadsheet_id"),
                            )
                            st.cache_data.clear()
                            st.session_state.email_sync_preview = None
                            st.success(f"✔ Successfully synced {processed} email transactions to your Google Sheet!")
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

        sup_engine = SheetsSyncEngine(client)
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
