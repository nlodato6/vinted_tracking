"""Metrics & Analytics Dashboard with Plotly for Vinted sales tracking."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from src.sheets import GoogleSheetsClient
from src.analytics import (
    parse_sold_dataframe,
    parse_active_dataframe,
    build_sales_trend_chart,
    build_category_chart,
    build_top_brands_chart,
    build_price_vs_profit_scatter,
    build_inventory_split_donuts,
    build_sale_types_chart,
)


@st.cache_data(ttl=60)
def load_raw_sheet_data(sheet_id: str, _creds):
    """Fetches raw rows from Google Sheets with a 60-second cache TTL."""
    client = GoogleSheetsClient(sheet_id=sheet_id, credentials=_creds)
    sh = client.get_spreadsheet()
    sold_rows = sh.worksheet("Sold_Items").get_all_values()
    active_rows = sh.worksheet("Item_Listings").get_all_values()
    return sold_rows, active_rows


def render_metrics_page():
    st.header("📈 InVintory Analytics & Performance")
    st.caption("From inbox alerts to bottom-line gains. Interactive metrics, profit trends, and inventory intelligence.")

    spreadsheet_id = st.session_state.get("spreadsheet_id")
    user_creds = st.session_state.get("user_credentials")

    if not spreadsheet_id:
        st.warning("No Google Spreadsheet ID configured. Please specify one in the sidebar.")
        return

    # Data fetch with spinner
    try:
        with st.spinner("Loading sales & inventory data from Google Sheets..."):
            sold_rows, active_rows = load_raw_sheet_data(spreadsheet_id, user_creds)
    except Exception as e:
        st.error(f"Failed to fetch data from spreadsheet: {e}")
        st.button("🔄 Retry Connection", on_click=lambda: st.cache_data.clear())
        return

    df_sold = parse_sold_dataframe(sold_rows)
    df_active = parse_active_dataframe(active_rows)

    if df_sold.empty and df_active.empty:
        st.info("No sales or inventory listings found in this spreadsheet yet.")
        return

    # -------------------------------------------------------------------------
    # Top Control Bar & Filters
    # -------------------------------------------------------------------------
    f1, f2, f3 = st.columns([1.5, 1.5, 1])

    with f1:
        time_options = ["All Time", "Last 30 Days", "Last 90 Days", "This Year"]
        selected_time = st.segmented_control("Timeframe", time_options, default="All Time")

    with f2:
        all_categories = sorted(list(set(df_sold["Category"].dropna().unique().tolist() + df_active["Category"].dropna().unique().tolist())))
        selected_cats = st.multiselect("Filter Categories", options=all_categories, default=[])

    with f3:
        st.write("") # vertical spacing
        if st.button("🔄 Refresh Data", width="stretch", help="Clear cache and fetch latest Google Sheets records"):
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
        if not filtered_sold.empty:
            filtered_sold = filtered_sold[filtered_sold["Category"].isin(selected_cats)]
        if not filtered_active.empty:
            filtered_active = filtered_active[filtered_active["Category"].isin(selected_cats)]

    # -------------------------------------------------------------------------
    # Financial KPI Cards
    # -------------------------------------------------------------------------
    total_rev = filtered_sold["sold_price_num"].sum() if not filtered_sold.empty else 0.0
    total_profit = filtered_sold["net_profit_num"].sum() if not filtered_sold.empty else 0.0
    items_sold = len(filtered_sold)
    avg_price = (total_rev / items_sold) if items_sold > 0 else 0.0
    margin_pct = ((total_profit / total_rev) * 100) if total_rev > 0 else 0.0

    active_items = len(filtered_active)
    active_value = filtered_active["current_price_num"].sum() if not filtered_active.empty else 0.0
    total_catalog = items_sold + active_items
    sell_through = ((items_sold / total_catalog) * 100) if total_catalog > 0 else 0.0

    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)

    kpi1.metric("Gross Revenue", f"${total_rev:,.2f}", border=True)
    kpi2.metric("Net Profit", f"${total_profit:,.2f}", border=True)
    kpi3.metric("Profit Margin", f"{margin_pct:.1f}%", border=True)
    kpi4.metric("Avg Sale Price", f"${avg_price:.2f}", border=True)
    kpi5.metric("Items Sold", f"{items_sold:,}", border=True)
    kpi6.metric("Active Catalog", f"${active_value:,.2f}", f"{active_items} items", border=True)

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
    # Row 5: Detailed Transactions Drill-Down Table
    # -------------------------------------------------------------------------
    with st.container(border=True):
        st.subheader("Recent Sales Drill-Down")
        
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
                label="📥 Export Filtered Sales to CSV",
                data=csv_data,
                file_name=f"vinted_sales_export_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                width="content",
            )
        else:
            st.info("No sold items matching current filters.")


if __name__ == "__main__":
    render_metrics_page()
else:
    render_metrics_page()
