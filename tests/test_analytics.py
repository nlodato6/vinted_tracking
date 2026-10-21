"""Unit tests for the analytics data cleaning and Plotly charts."""

import pandas as pd
import pytest
from src.analytics import (
    clean_currency,
    clean_percent,
    parse_sold_dataframe,
    parse_active_dataframe,
    build_sales_trend_chart,
    build_category_chart,
    build_top_brands_chart,
    build_price_vs_profit_scatter,
    build_inventory_split_donuts,
    build_sale_types_chart,
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
