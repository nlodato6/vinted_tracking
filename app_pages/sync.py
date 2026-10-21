"""Sync & Batch Import page for Vinted sales and inventory tracking."""

import os
import time
import streamlit as st
import pandas as pd

from src.sheets import GoogleSheetsClient, SheetsSyncEngine
from src.html_parser import parse_closet_html, parse_orders_html, detect_html_type
from src.email_parser import fetch_vinted_emails_via_imap
from src.notifications import record_html_parse_event, record_email_parse_event, get_sync_status


def get_sheets_client() -> GoogleSheetsClient:
    """Returns a GoogleSheetsClient configured for the current session user."""
    return GoogleSheetsClient(
        sheet_id=st.session_state.spreadsheet_id,
        credentials=st.session_state.user_credentials,
    )


def render_sync_page():
    st.header("⚡ Sync & Batch Inventory Operations")
    st.caption("Reconcile your Vinted closet via HTML files or sync new sales directly from your Gmail inbox.")

    client = get_sheets_client()
    status_state = get_sync_status()

    try:
        sh = client.get_spreadsheet()
        ws_l = sh.worksheet("Item_Listings")
        ws_s = sh.worksheet("Sold_Items")
        active_cnt = max(0, len(ws_l.get_all_values()) - 1)
        sold_cnt = max(0, len(ws_s.get_all_values()) - 1)
        connected = True
    except Exception:
        active_cnt = 0
        sold_cnt = 0
        connected = False

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

            st.dataframe(pd.DataFrame(files_info), width="stretch")

            # Reconciliation Preview
            if parsed_orders or parsed_closet:
                st.divider()
                st.markdown("#### ⚡ Reconciliation Preview (Zero-Duplication)")
                
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
                    p1.metric("🟢 New Active to Add", len(new_listings), border=True)
                    p2.metric("🔵 Active Moving to Sold", len(sold_transitions), border=True)
                    p3.metric("⚪ Duplicates Skipped", len(already_sold) + (len(parsed_closet) - len(new_listings)), border=True)

                    if sold_transitions:
                        with st.expander("View Items Moving to Sold", expanded=False):
                            st.dataframe(pd.DataFrame([{"Title": o.item_title, "Price": f"${o.sold_price:.2f}", "Order ID": o.order_id} for o in sold_transitions]), width="stretch")

                    if new_listings:
                        with st.expander("View New Listings to Add", expanded=False):
                            st.dataframe(pd.DataFrame([{"Title": i.title, "Brand": i.brand, "Price": f"${i.posted_price:.2f}", "Category": i.category} for i in new_listings]), width="stretch")

                    if st.button("🚀 Commit & Synchronize to Google Sheets", type="primary", width="stretch"):
                        with st.spinner("Synchronizing to Google Sheets..."):
                            res_orders = engine.sync_orders(parsed_orders) if parsed_orders else {"synced_orders": 0}
                            res_closet = engine.sync_listings(parsed_closet) if parsed_closet else {"updated": 0, "created": 0}
                            engine.sync_lots()
                            record_html_parse_event(details=f"Web: {len(uploaded_files)} files ({len(parsed_orders)} orders, {len(parsed_closet)} items)", client=client)
                            st.cache_data.clear()

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

            if st.button("🔄 Sync Vinted Emails Now", type="primary", width="stretch"):
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
                                st.cache_data.clear()
                                st.dataframe(pd.DataFrame(event_rows), width="stretch")
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


if __name__ == "__main__":
    render_sync_page()
else:
    render_sync_page()
