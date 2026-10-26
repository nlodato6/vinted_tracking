"""Metrics & Analytics Dashboard with Plotly for Vinted sales tracking."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from src.sheets import GoogleSheetsClient
from src.telemetry import log_event, log_error
from src.analytics import (
    clean_currency,
    parse_sold_dataframe,
    parse_active_dataframe,
    parse_cancelled_dataframe,
    build_sales_trend_chart,
    build_category_chart,
    build_top_brands_chart,
    build_price_vs_profit_scatter,
    build_inventory_split_donuts,
    build_sale_types_chart,
    compute_net_cash_profit,
    build_cash_flow_waterfall,
    build_cancellation_reasons_chart,
    build_cancellation_timeline_chart,
    parse_lots_dataframe,
    compute_lots_performance,
    build_lot_spent_vs_recouped_chart,
)


@st.cache_data(ttl=60)
def load_raw_sheet_data(sheet_id: str, _creds):
    """Fetches raw rows and expense data from Google Sheets in a single batch get (1 API call)."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    sh = client.get_spreadsheet()
    try:
        res = sh.values_batch_get([
            "Sold_Items!A:Y",
            "Item_Listings!A:R",
            "Inventory_Lots!A:I",
            "Shipping_Supplies!A:H",
            "Cancelled_Orders!A:L",
            "Hidden_Items!A:S",
        ])
        val_ranges = res.get("valueRanges", [])
        sold_rows = val_ranges[0].get("values", []) if len(val_ranges) > 0 else []
        active_rows = val_ranges[1].get("values", []) if len(val_ranges) > 1 else []
        lot_rows = val_ranges[2].get("values", []) if len(val_ranges) > 2 else []
        supply_rows = val_ranges[3].get("values", []) if len(val_ranges) > 3 else []
        cancelled_rows = val_ranges[4].get("values", []) if len(val_ranges) > 4 else []
        hidden_rows = val_ranges[5].get("values", []) if len(val_ranges) > 5 else []
        return sold_rows, active_rows, lot_rows, supply_rows, cancelled_rows, hidden_rows
    except Exception:
        sold_rows = sh.worksheet("Sold_Items").get_all_values()
        active_rows = sh.worksheet("Item_Listings").get_all_values()
        try:
            lot_rows = sh.worksheet("Inventory_Lots").get_all_values()
        except Exception:
            lot_rows = []
        try:
            supply_rows = sh.worksheet("Shipping_Supplies").get_all_values()
        except Exception:
            supply_rows = []
        try:
            cancelled_rows = sh.worksheet("Cancelled_Orders").get_all_values()
        except Exception:
            cancelled_rows = []
        try:
            hidden_rows = sh.worksheet("Hidden_Items").get_all_values()
        except Exception:
            hidden_rows = []
        return sold_rows, active_rows, lot_rows, supply_rows, cancelled_rows, hidden_rows


def render_metrics_page():
    st.header("Thread Count Analytics & Performance", icon=":material/analytics:")
    st.caption("From inbox alerts to bottom-line gains. Interactive metrics, profit trends, and inventory intelligence.")

    spreadsheet_id = st.session_state.get("spreadsheet_id")
    user_creds = st.session_state.get("user_credentials")
    u_email = (st.session_state.get("user_info", {}).get("email") or "anonymous").lower()

    if not spreadsheet_id:
        st.warning("No Google Spreadsheet ID configured. Please specify one in the sidebar.", icon=":material/warning:")
        return

    # Data fetch with spinner
    try:
        with st.spinner("Loading sales & inventory data from Google Sheets..."):
            sold_rows, active_rows, lot_rows, supply_rows, cancelled_rows, hidden_rows = load_raw_sheet_data(spreadsheet_id, user_creds)
        log_event(u_email, "view_metrics", f"Loaded data for sheet {spreadsheet_id}", spreadsheet_id=spreadsheet_id)
    except Exception as e:
        log_error(u_email, "metrics.py:fetch_data", e)
        st.error(f"Failed to fetch data from spreadsheet: {e}", icon=":material/error:")
        if not user_creds:
            st.info(
                "**Why did this happen?** You signed in using the Manual Password fallback instead of Google OAuth.\n\n"
                "To access Google Sheets, you can either:\n"
                "1. **Sign out and click ':material/key: Sign in with Google'** (easiest, no service account needed).\n"
                "2. **Add `GOOGLE_SERVICE_ACCOUNT_JSON` to Streamlit Cloud Secrets** with your `credentials.json` contents so manual logins can query Google Sheets.",
                icon=":material/lightbulb:",
            )
        st.button("Retry Connection", icon=":material/refresh:", on_click=lambda: st.cache_data.clear())
        return

    df_sold = parse_sold_dataframe(sold_rows)
    df_active = parse_active_dataframe(active_rows)
    df_cancelled = parse_cancelled_dataframe(cancelled_rows)
    hidden_count = max(0, len(hidden_rows) - 1) if hidden_rows else 0

    if df_sold.empty and df_active.empty and df_cancelled.empty:
        st.info("No sales or inventory listings found in this spreadsheet yet.")
        return

    # Guarantee canonical columns exist even if Google Sheet headers were customized or empty
    if not df_sold.empty:
        for col, default_val in [
            ("Category", "Other Apparel"),
            ("Brand", "Unbranded"),
            ("Sale_Type", "Original Price"),
            ("Title", "Untitled Item"),
            ("sold_price_num", 0.0),
            ("net_profit_num", 0.0),
            ("margin_pct_num", 0.0),
        ]:
            if col not in df_sold.columns:
                df_sold[col] = default_val

    if not df_active.empty:
        for col, default_val in [
            ("Category", "Other Apparel"),
            ("Brand", "Unbranded"),
            ("Title", "Untitled Item"),
            ("current_price_num", 0.0),
            ("posted_price_num", 0.0),
        ]:
            if col not in df_active.columns:
                df_active[col] = default_val

    # -------------------------------------------------------------------------
    # Top Control Bar & Filters
    # -------------------------------------------------------------------------
    f1, f2, f3 = st.columns([1.5, 1.5, 1])

    with f1:
        time_options = ["All Time", "Last 30 Days", "Last 90 Days", "This Year"]
        selected_time = st.segmented_control("Timeframe", time_options, default="All Time")

    with f2:
        sold_cats = df_sold["Category"].dropna().unique().tolist() if (not df_sold.empty and "Category" in df_sold.columns) else []
        active_cats = df_active["Category"].dropna().unique().tolist() if (not df_active.empty and "Category" in df_active.columns) else []
        all_categories = sorted(list(set(sold_cats + active_cats)))
        selected_cats = st.multiselect("Filter Categories", options=all_categories, default=[])

    with f3:
        st.write("") # vertical spacing
        if st.button("Refresh Data", icon=":material/refresh:", width="stretch", help="Clear cache and fetch latest Google Sheets records"):
            st.cache_data.clear()
            st.rerun()

    # Apply Filters to DataFrames
    filtered_sold = df_sold.copy()
    filtered_active = df_active.copy()

    # Date filter on sold items
    if not filtered_sold.empty and "sale_date_dt" in filtered_sold.columns:
        now = datetime.now()
        if selected_time == "Last 30 Days":
            cutoff = now - timedelta(days=30)
            filtered_sold = filtered_sold[filtered_sold["sale_date_dt"] >= cutoff]
        elif selected_time == "Last 90 Days":
            cutoff = now - timedelta(days=90)
            filtered_sold = filtered_sold[filtered_sold["sale_date_dt"] >= cutoff]
        elif selected_time == "This Year":
            cutoff = datetime(now.year, 1, 1)
            filtered_sold = filtered_sold[filtered_sold["sale_date_dt"] >= cutoff]

    # Category filter
    if selected_cats:
        if not filtered_sold.empty and "Category" in filtered_sold.columns:
            filtered_sold = filtered_sold[filtered_sold["Category"].isin(selected_cats)]
        if not filtered_active.empty and "Category" in filtered_active.columns:
            filtered_active = filtered_active[filtered_active["Category"].isin(selected_cats)]

    # -------------------------------------------------------------------------
    # Financial KPI Cards & Actual Net Cash Profit
    # -------------------------------------------------------------------------
    total_rev = filtered_sold["sold_price_num"].sum() if (not filtered_sold.empty and "sold_price_num" in filtered_sold.columns) else 0.0
    total_profit = filtered_sold["net_profit_num"].sum() if (not filtered_sold.empty and "net_profit_num" in filtered_sold.columns) else 0.0
    items_sold = len(filtered_sold)
    avg_price = (total_rev / items_sold) if items_sold > 0 else 0.0
    margin_pct = ((total_profit / total_rev) * 100) if total_rev > 0 else 0.0

    active_items = len(filtered_active)
    active_value = filtered_active["current_price_num"].sum() if (not filtered_active.empty and "current_price_num" in filtered_active.columns) else 0.0
    total_catalog = items_sold + active_items
    sell_through = ((items_sold / total_catalog) * 100) if total_catalog > 0 else 0.0

    # Calculate Total Invested Capital (Lots + Supplies) and Actual Net Cash Profit
    lot_cost_idx = 3
    if lot_rows and len(lot_rows) > 0 and "Total_Cost" in lot_rows[0]:
        lot_cost_idx = lot_rows[0].index("Total_Cost")
    total_lots_invested = sum(clean_currency(r[lot_cost_idx]) for r in lot_rows[1:] if len(r) > lot_cost_idx) if lot_rows and len(lot_rows) > 1 else 0.0

    sup_cost_idx = 5
    if supply_rows and len(supply_rows) > 0 and "Total_Cost" in supply_rows[0]:
        sup_cost_idx = supply_rows[0].index("Total_Cost")
    total_supplies_invested = sum(clean_currency(r[sup_cost_idx]) for r in supply_rows[1:] if len(r) > sup_cost_idx) if supply_rows and len(supply_rows) > 1 else 0.0

    cash_summary = compute_net_cash_profit(total_rev, total_lots_invested, total_supplies_invested)
    net_cash = cash_summary["actual_net_cash_profit"]
    is_positive = cash_summary["is_positive"]
    total_invested = cash_summary["total_invested"]

    # Calculate unsold inventory acquisition cost
    act_cost_idx = 5
    if active_rows and len(active_rows) > 0 and "Unit_Cost" in active_rows[0]:
        act_cost_idx = active_rows[0].index("Unit_Cost")
    unsold_cost_basis = sum(clean_currency(r[act_cost_idx]) for r in active_rows[1:] if len(r) > act_cost_idx) if active_rows and len(active_rows) > 1 else 0.0

    # Calculate lot-level metrics (Spent vs. Recouped)
    df_lots_perf, lot_summary = compute_lots_performance(lot_rows, df_sold, df_active)

    # Primary Row: 4 Core Financial Indicators
    k1, k2, k3, k4 = st.columns(4)

    if is_positive:
        cash_delta = f":material/check_circle: Cash Flow Positive (+{cash_summary['cash_roi_pct']:.1f}% ROI)"
    else:
        cash_delta = f":material/warning: ${abs(net_cash):,.2f} to breakeven ({cash_summary['recovery_pct']:.1f}% recovered)"

    k1.metric(
        "Actual Net Cash Profit",
        f"${net_cash:,.2f}",
        delta=cash_delta,
        delta_color="normal" if is_positive else "inverse",
        help="Actual cash in your bank account today: Gross Revenue minus all upfront capital spent on Inventory Lots and Shipping Supplies.",
        border=True,
    )
    k2.metric(
        "Gross Revenue",
        f"${total_rev:,.2f}",
        help="Total gross payments received from all completed orders.",
        border=True,
    )
    k3.metric(
        "Realized Net Profit",
        f"${total_profit:,.2f}",
        f"{margin_pct:.1f}% margin",
        help="Bookkeeping net profit: Revenue minus allocated unit cost and packaging deduction for items that have sold.",
        border=True,
    )
    k4.metric(
        "Total Capital Invested",
        f"${total_invested:,.2f}",
        f"Lots: ${total_lots_invested:,.2f} | Supplies: ${total_supplies_invested:,.2f}",
        delta_color="off",
        help="Total cash outlay spent to acquire bulk inventory hauls plus packaging supplies.",
        border=True,
    )

    # Secondary Row: Volume & Catalog Metrics
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Items Sold", f"{items_sold:,} items", border=True)
    v2.metric("Avg Sale Price", f"${avg_price:.2f}", border=True)
    v3.metric("Active Catalog Value", f"${active_value:,.2f}", f"{active_items} listed", border=True)
    v4.metric("Sell-Through Rate", f"{sell_through:.1f}%", f"{items_sold} of {total_catalog} items", border=True)

    # Row 3: Cancellations & Vault Inventory (Hidden Items)
    cancelled_count = len(df_cancelled) if not df_cancelled.empty else 0
    lost_revenue = df_cancelled["sold_price_num"].sum() if (not df_cancelled.empty and "sold_price_num" in df_cancelled.columns) else 0.0
    cancellation_rate = (cancelled_count / (items_sold + cancelled_count) * 100) if (items_sold + cancelled_count) > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Cancellation Rate",
        f"{cancellation_rate:.1f}%",
        f"{cancelled_count} cancelled" if cancelled_count else "0 cancellations",
        delta_color="inverse" if cancelled_count else "off",
        help="Percentage of orders that were cancelled or refunded.",
        border=True,
    )
    c2.metric(
        "Lost Gross Revenue",
        f"${lost_revenue:,.2f}",
        "Refunded to buyers" if lost_revenue > 0 else "No lost revenue",
        delta_color="inverse" if lost_revenue > 0 else "off",
        help="Gross revenue lost to cancellations and buyer refunds.",
        border=True,
    )
    c3.metric(
        "Cancelled Units",
        f"{cancelled_count:,} orders",
        help="Total number of orders cancelled on Vinted.",
        border=True,
    )
    c4.metric(
        "Hidden Vault Items",
        f"{hidden_count:,} items",
        "Archived / drafts",
        delta_color="off",
        help="Listings currently hidden or saved in draft/vault status.",
        border=True,
    )

    # -------------------------------------------------------------------------
    # Cash Flow & Capital Recovery Section
    # -------------------------------------------------------------------------
    with st.container(border=True):
        st.subheader("Cash Flow & Capital Recovery", icon=":material/payments:")
        st.caption("Track when your upfront haul and packaging investments are fully recouped into pure take-home profit.")

        cf_col1, cf_col2 = st.columns([1.2, 1.8], vertical_alignment="center")

        with cf_col1:
            rec_pct = min(100.0, max(0.0, cash_summary["recovery_pct"]))
            st.markdown(f"**Capital Recovery Progress: {cash_summary['recovery_pct']:.1f}%**")
            st.progress(rec_pct / 100.0)

            if is_positive:
                st.success(
                    f"**Hauls & Supplies 100% Paid Off!** You are in pure cash profit territory with **+${net_cash:,.2f}** in pocket.",
                    icon=":material/check_circle:",
                )
            else:
                st.info(
                    f"**Breakeven Target**: You need **${abs(net_cash):,.2f}** more in sales revenue to fully recoup all inventory and packaging expenses.",
                    icon=":material/target:",
                )

            st.markdown(f"""
            - **Gross Cash Inflow**: `${total_rev:,.2f}`
            - **Inventory Hauls Outflow**: `${total_lots_invested:,.2f}`
            - **Shipping Supplies Outflow**: `${total_supplies_invested:,.2f}`
            - **Unsold Closet Cost Basis**: `${unsold_cost_basis:,.2f}` *({active_items} active items ready to sell)*
            """)

        with cf_col2:
            fig_waterfall = build_cash_flow_waterfall(cash_summary)
            st.plotly_chart(fig_waterfall, width="stretch", alt="Cash flow waterfall breakdown")

    # -------------------------------------------------------------------------
    # Inventory Lot Performance: Spent vs. Recouped Section
    # -------------------------------------------------------------------------
    with st.container(border=True):
        st.subheader("Inventory Lot Performance: Spent vs. Recouped", icon=":material/inventory_2:")
        st.caption("Track how much capital was invested into bulk inventory lots versus how much sales revenue has been recouped so far.")

        lp_k1, lp_k2, lp_k3, lp_k4 = st.columns(4)

        lot_spent = lot_summary["total_spent"]
        lot_recouped = lot_summary["total_recouped"]
        lot_net = lot_summary["total_net"]
        lot_rec_pct = lot_summary["overall_recovery_pct"]
        lot_is_pos = lot_summary["is_positive"]

        lp_k1.metric(
            "Lots Capital Spent",
            f"${lot_spent:,.2f}",
            f"{lot_summary['total_lots']} total lots",
            delta_color="off",
            help="Total acquisition cost spent across all inventory lots in Inventory_Lots.",
            border=True,
        )
        lp_k2.metric(
            "Revenue Recouped",
            f"${lot_recouped:,.2f}",
            f"{lot_rec_pct:.1f}% recovered",
            delta_color="normal" if lot_is_pos else "off",
            help="Total gross sales revenue generated by items associated with your inventory lots.",
            border=True,
        )
        lp_k3.metric(
            "Net Lot Return",
            f"${abs(lot_net):,.2f}",
            f"{'+' if lot_is_pos else '-'}${abs(lot_net):,.2f} {'profit' if lot_is_pos else 'to breakeven'}",
            delta_color="normal" if lot_is_pos else "inverse",
            help="Total Revenue Recouped minus Total Capital Spent across all lots.",
            border=True,
        )
        lp_k4.metric(
            "Profitable Lots",
            f"{lot_summary['profitable_lots']} of {lot_summary['total_lots']}",
            f"{lot_summary['in_progress_lots']} in progress",
            delta_color="normal" if lot_summary['profitable_lots'] > 0 else "off",
            help="Number of lots that have recouped 100% of their upfront cost and are in pure profit.",
            border=True,
        )

        if not df_lots_perf.empty:
            lc1, lc2 = st.columns([1.4, 1.6], vertical_alignment="top")

            with lc1:
                fig_lot_bar = build_lot_spent_vs_recouped_chart(df_lots_perf)
                st.plotly_chart(fig_lot_bar, width="stretch", alt="Spent vs recouped comparison by inventory lot")

            with lc2:
                st.markdown("**Lot Breakdown Table**")
                disp_cols = [
                    "Lot_ID", "Vendor", "Total_Spent", "Total_Recouped",
                    "Net_Profit_Deficit", "Recovery_%", "Items_Sold", "Active_Items"
                ]
                col_cfg = {
                    "Lot_ID": st.column_config.TextColumn("Lot ID"),
                    "Vendor": st.column_config.TextColumn("Vendor / Source"),
                    "Total_Spent": st.column_config.NumberColumn("Spent", format="$%.2f"),
                    "Total_Recouped": st.column_config.NumberColumn("Recouped", format="$%.2f"),
                    "Net_Profit_Deficit": st.column_config.NumberColumn("Net Cash", format="$%.2f"),
                    "Recovery_%": st.column_config.NumberColumn("Recovery %", format="%.1f%%"),
                    "Items_Sold": st.column_config.NumberColumn("Sold", format="%d"),
                    "Active_Items": st.column_config.NumberColumn("Active", format="%d"),
                }
                st.dataframe(
                    df_lots_perf[disp_cols],
                    column_config=col_cfg,
                    hide_index=True,
                    width="stretch",
                    alt="Table of lot spent vs recouped performance",
                )
        else:
            st.info("No inventory lots defined in the `Inventory_Lots` sheet yet.", icon=":material/info:")

    st.divider()

    # -------------------------------------------------------------------------
    # Row 1: Sales & Net Profit Timeline (Plotly)
    # -------------------------------------------------------------------------
    with st.container(border=True):
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.subheader("Sales & Net Profit Timeline")
        with col_t2:
            trend_freq = st.segmented_control("Grouping", ["Daily", "Weekly", "Monthly"], default="Daily")
            freq_code = "W" if trend_freq == "Weekly" else ("M" if trend_freq == "Monthly" else "D")

        fig_trend = build_sales_trend_chart(filtered_sold, freq=freq_code)
        st.plotly_chart(fig_trend, width="stretch", alt="Sales and profit progression over time")

    # -------------------------------------------------------------------------
    # Row 2: Category Breakdown & Top Brands (Plotly)
    # -------------------------------------------------------------------------
    c1, c2 = st.columns(2)

    with c1:
        with st.container(border=True):
            st.subheader("Category Breakdown")
            fig_cat = build_category_chart(filtered_sold)
            st.plotly_chart(fig_cat, width="stretch", alt="Sales and net profit by clothing category")

    with c2:
        with st.container(border=True):
            st.subheader("Top 10 Reseller Brands")
            fig_brands = build_top_brands_chart(filtered_sold, top_n=10)
            st.plotly_chart(fig_brands, width="stretch", alt="Top brands ranked by net profit")

    # -------------------------------------------------------------------------
    # Row 3: Price vs Profit Scatter Analysis (Plotly)
    # -------------------------------------------------------------------------
    with st.container(border=True):
        st.subheader("Price vs. Profit Distribution")
        fig_scatter = build_price_vs_profit_scatter(filtered_sold)
        st.plotly_chart(fig_scatter, width="stretch", alt="Scatter plot of sold price vs net profit by category")

    # -------------------------------------------------------------------------
    # Row 4: Inventory Donut Splits & Negotiation Types
    # -------------------------------------------------------------------------
    d1, d2, d3 = st.columns(3)

    fig_split, fig_val = build_inventory_split_donuts(filtered_sold, filtered_active)
    fig_types = build_sale_types_chart(filtered_sold)

    with d1:
        with st.container(border=True):
            st.plotly_chart(fig_split, width="stretch", alt="Sell through ratio comparison")

    with d2:
        with st.container(border=True):
            st.plotly_chart(fig_val, width="stretch", alt="Active inventory valuation share by category")

    with d3:
        with st.container(border=True):
            st.plotly_chart(fig_types, width="stretch", alt="Sales by negotiation type")

    # -------------------------------------------------------------------------
    # Row 5: Order Cancellation Intelligence & Lost Revenue
    # -------------------------------------------------------------------------
    if not df_cancelled.empty:
        with st.container(border=True):
            st.subheader("Cancellation Intelligence & Lost Revenue", icon=":material/cancel:")
            st.caption("Understand why sales fall through, identify repeat return patterns, and track refunded revenue.")

            canc_col1, canc_col2 = st.columns(2)
            with canc_col1:
                fig_reasons = build_cancellation_reasons_chart(df_cancelled)
                st.plotly_chart(fig_reasons, width="stretch", alt="Cancelled orders by reason")
            with canc_col2:
                fig_time = build_cancellation_timeline_chart(df_cancelled)
                st.plotly_chart(fig_time, width="stretch", alt="Monthly cancellation trend")

            with st.expander(f"View Cancelled Orders Log ({cancelled_count} orders)", expanded=False):
                disp_canc_cols = [c for c in ["Order_ID", "Item_SKU", "Title", "Brand", "Category", "Sold_Price", "Cancellation_Reason", "Buyer_Username", "Source"] if c in df_cancelled.columns]
                st.dataframe(df_cancelled[disp_canc_cols], hide_index=True, width="stretch")

    # -------------------------------------------------------------------------
    # Row 6: Detailed Transactions Drill-Down Table
    # -------------------------------------------------------------------------
    with st.container(border=True):
        st.subheader("Recent Sales Drill-Down", icon=":material/table_chart:")
        
        if not filtered_sold.empty:
            display_cols = [
                "Item_SKU", "Title", "Brand", "Category", 
                "Sold_Price", "Packaging_Cost", "Net_Profit", "Margin_%", 
                "Sale_Type", "Sale_Date", "Order_ID"
            ]
            valid_cols = [c for c in display_cols if c in filtered_sold.columns]
            
            st.dataframe(
                filtered_sold[valid_cols],
                hide_index=True,
                width="stretch",
                alt="Detailed list of filtered sold transactions",
            )

            # Export CSV
            csv_data = filtered_sold[valid_cols].to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Export Filtered Sales to CSV",
                icon=":material/download:",
                data=csv_data,
                file_name=f"vinted_sales_export_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                width="content",
            )
        else:
            st.info("No sold items matching current filters.", icon=":material/info:")


if __name__ == "__main__":
    render_metrics_page()
else:
    render_metrics_page()
