"""Unit tests for the analytics data cleaning and Plotly charts."""

import pandas as pd
import pytest
from src.analytics import (
    clean_currency,
    clean_percent,
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
)


def test_clean_currency():
    assert clean_currency("$14.50") == 14.50
    assert clean_currency("12.00") == 12.00
    assert clean_currency("-$3.50") == -3.50
    assert clean_currency("") == 0.0
    assert clean_currency(None) == 0.0
    assert clean_currency("invalid") == 0.0


def test_clean_percent():
    assert clean_percent("78.5%") == 78.5
    assert clean_percent("100%") == 100.0
    assert clean_percent("0.0%") == 0.0
    assert clean_percent("") == 0.0
    assert clean_percent(None) == 0.0


def test_parse_sold_dataframe_and_charts():
    raw_rows = [
        ["Item_SKU", "Title", "Brand", "Category", "Unit_Cost", "Sold_Price", "Net_Profit", "Margin_%", "Sale_Type", "Sale_Date"],
        ["SKU-001", "Graphic Tee", "Nike", "Tops & Shirts", "$1.00", "$15.00", "$12.00", "80.0%", "Original Price", "2026-10-15"],
        ["SKU-002", "Blue Jeans", "Levi's", "Pants & Jeans", "$2.50", "$25.00", "$18.50", "74.0%", "Offer", "2026-10-16"],
        ["SKU-003", "Vintage Hoodie", "Nike", "Sweaters & Hoodies", "$3.00", "$30.00", "$24.00", "80.0%", "Offer", "2026-10-18"],
    ]

    df = parse_sold_dataframe(raw_rows)
    assert len(df) == 3
    assert df["sold_price_num"].sum() == 70.00
    assert df["net_profit_num"].sum() == 54.50
    assert df["unit_cost_num"].iloc[0] == 1.00
    assert pd.api.types.is_datetime64_any_dtype(df["sale_date_dt"])

    # Test all Plotly builders
    fig_trend = build_sales_trend_chart(df)
    assert fig_trend is not None
    assert len(fig_trend.data) == 3  # Revenue, Profit, Cumulative

    fig_cat = build_category_chart(df)
    assert fig_cat is not None

    fig_brands = build_top_brands_chart(df, top_n=5)
    assert fig_brands is not None

    fig_scatter = build_price_vs_profit_scatter(df)
    assert fig_scatter is not None

    fig_types = build_sale_types_chart(df)
    assert fig_types is not None


def test_parse_active_dataframe_and_donuts():
    raw_active = [
        ["Item_SKU", "Title", "Brand", "Category", "Unit_Cost", "Posted_Price", "Current_Price", "Favorites", "Views"],
        ["SKU-101", "Cargo Shorts", "Carhartt", "Pants & Jeans", "$2.00", "$20.00", "$18.00", "5", "42"],
        ["SKU-102", "Windbreaker", "Adidas", "Outerwear", "$4.00", "$35.00", "$32.00", "12", "110"],
    ]

    raw_sold = [
        ["Item_SKU", "Title", "Brand", "Category", "Unit_Cost", "Sold_Price", "Net_Profit", "Margin_%", "Sale_Type", "Sale_Date"],
        ["SKU-001", "Graphic Tee", "Nike", "Tops & Shirts", "$1.00", "$15.00", "$12.00", "80.0%", "Original Price", "2026-10-15"],
    ]

    df_active = parse_active_dataframe(raw_active)
    df_sold = parse_sold_dataframe(raw_sold)

    assert len(df_active) == 2
    assert df_active["current_price_num"].sum() == 50.00
    assert df_active["favorites_num"].sum() == 17

    fig1, fig2 = build_inventory_split_donuts(df_sold, df_active)
    assert fig1 is not None
    assert fig2 is not None


def test_empty_dataframe_handling():
    empty_df = pd.DataFrame()
    fig1 = build_sales_trend_chart(empty_df)
    fig2 = build_category_chart(empty_df)
    fig3 = build_top_brands_chart(empty_df)
    fig4 = build_price_vs_profit_scatter(empty_df)
    fig5, fig6 = build_inventory_split_donuts(empty_df, empty_df)
    fig7 = build_sale_types_chart(empty_df)

    assert fig1 is not None
    assert fig2 is not None
    assert fig3 is not None
    assert fig4 is not None
    assert fig5 is not None
    assert fig6 is not None
    assert fig7 is not None


def test_compute_net_cash_profit():
    # Case 1: Positive Cash Flow
    res_pos = compute_net_cash_profit(
        total_revenue=250.0,
        total_lots_invested=100.0,
        total_supplies_invested=20.0,
    )
    assert res_pos["total_revenue"] == 250.0
    assert res_pos["total_invested"] == 120.0
    assert res_pos["actual_net_cash_profit"] == 130.0
    assert res_pos["is_positive"] is True
    assert res_pos["cash_roi_pct"] > 100.0

    # Case 2: Negative Cash Flow (Recovering upfront spend)
    res_neg = compute_net_cash_profit(
        total_revenue=50.0,
        total_lots_invested=100.0,
        total_supplies_invested=20.0,
    )
    assert res_neg["actual_net_cash_profit"] == -70.0
    assert res_neg["is_positive"] is False
    assert round(res_neg["recovery_pct"], 1) == 41.7

    # Case 3: Waterfall figure generation
    fig_pos = build_cash_flow_waterfall(res_pos)
    assert fig_pos is not None
    assert len(fig_pos.data) == 1

    fig_neg = build_cash_flow_waterfall(res_neg)
    assert fig_neg is not None


def test_missing_and_misaligned_headers_robustness():
    # Case where sheet has NO Category column and lowercase headers
    raw_sold = [
        ["sku", "title", "sold_price", "net_profit"],
        ["SKU-1", "Cool Shirt", "$20.00", "$15.00"],
    ]
    df_sold = parse_sold_dataframe(raw_sold)
    assert "Category" in df_sold.columns
    assert df_sold["Category"].iloc[0] == "Other Apparel"
    assert "Brand" in df_sold.columns
    assert df_sold["Brand"].iloc[0] == "Unbranded"
    assert "Sale_Type" in df_sold.columns
    assert df_sold["sold_price_num"].iloc[0] == 20.00

    raw_active = [
        ["sku", "title", "current_price"],
        ["SKU-2", "Vintage Jeans", "$45.00"],
    ]
    df_active = parse_active_dataframe(raw_active)
    assert "Category" in df_active.columns
    assert df_active["Category"].iloc[0] == "Other Apparel"
    assert "Brand" in df_active.columns
    assert df_active["current_price_num"].iloc[0] == 45.00

    # Ensure charts still build cleanly with no exceptions
    fig_scatter = build_price_vs_profit_scatter(df_sold)
    assert fig_scatter is not None

    fig_cat = build_category_chart(df_sold)
    assert fig_cat is not None


def test_blank_rows_handling():
    # Verify that completely blank rows or rows with empty cells are omitted
    raw_active_with_blanks = [
        ["Item_SKU", "Lot_ID", "Title", "Brand", "Current_Price"],
        ["SKU-1001", "LOT-001", "Valid Jacket", "Nike", "$50.00"],
        ["", "", "", "", ""],
        ["", "   ", "", "  ", ""],
        ["SKU-1002", "LOT-001", "Valid Pants", "Levis", "$30.00"],
    ]
    df_active = parse_active_dataframe(raw_active_with_blanks)
    assert len(df_active) == 2
    assert list(df_active["Title"]) == ["Valid Jacket", "Valid Pants"]

    raw_sold_with_blanks = [
        ["Item_SKU", "Title", "Sold_Price", "Sale_Date"],
        ["SKU-1001", "Sold Sweater", "$40.00", "2026-10-20"],
        ["", "", "", ""],
        ["SKU-1002", "Sold Shoes", "$60.00", "2026-10-21"],
    ]
    df_sold = parse_sold_dataframe(raw_sold_with_blanks)
    assert len(df_sold) == 2
    assert list(df_sold["Title"]) == ["Sold Sweater", "Sold Shoes"]


def test_charts_with_missing_columns_do_not_throw_keyerror():
    # DataFrame that has NO Category, NO Brand, NO numeric columns
    sparse_df = pd.DataFrame([{"Arbitrary_Col": "Value1"}, {"Arbitrary_Col": "Value2"}])

    # None of these chart builders should raise KeyError
    assert build_sales_trend_chart(sparse_df) is not None
    assert build_category_chart(sparse_df) is not None
    assert build_top_brands_chart(sparse_df) is not None
    assert build_price_vs_profit_scatter(sparse_df) is not None
    assert build_sale_types_chart(sparse_df) is not None
    d1, d2 = build_inventory_split_donuts(sparse_df, sparse_df)
    assert d1 is not None and d2 is not None


def test_parse_cancelled_dataframe_and_charts():
    raw_rows = [
        ["Order_ID", "Item_SKU", "Title", "Brand", "Category", "Sold_Price", "Original_Gross", "Cancellation_Date", "Cancellation_Reason", "Buyer_Username", "Vinted_URL", "Source"],
        ["ORD-C1", "SKU-101", "Jeans", "Levis", "Pants", "$30.00", "$30.00", "2026-10-10", "Buyer cancelled", "buyer1", "", "HTML"],
        ["ORD-C2", "SKU-102", "Boots", "UGG", "Shoes", "$50.00", "$50.00", "2026-10-12", "Out of stock", "buyer2", "", "Email"],
    ]

    df_canc = parse_cancelled_dataframe(raw_rows)
    assert len(df_canc) == 2
    assert df_canc["sold_price_num"].sum() == 80.00
    assert df_canc["original_gross_num"].sum() == 80.00
    assert pd.api.types.is_datetime64_any_dtype(df_canc["cancel_date_dt"])

    fig_reasons = build_cancellation_reasons_chart(df_canc)
    assert fig_reasons is not None

    fig_timeline = build_cancellation_timeline_chart(df_canc)
    assert fig_timeline is not None

    # Empty DataFrame tests
    empty_df = pd.DataFrame()
    assert build_cancellation_reasons_chart(empty_df) is not None
    assert build_cancellation_timeline_chart(empty_df) is not None



