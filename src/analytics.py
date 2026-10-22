"""Analytics and Plotly visualization engine for Vinted sales tracking."""

import re
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def clean_currency(val: Any) -> float:
    """Extracts a clean float from currency strings like '$14.50' or returns 0.0."""
    if val is None or pd.isna(val):
        return 0.0
    val_str = str(val).strip()
    cleaned = re.sub(r"[^\d.-]", "", val_str)
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def clean_percent(val: Any) -> float:
    """Extracts a percentage float like '78.5%' -> 78.5 or returns 0.0."""
    if val is None or pd.isna(val):
        return 0.0
    val_str = str(val).strip()
    cleaned = re.sub(r"[^\d.-]", "", val_str)
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_sold_dataframe(rows: list) -> pd.DataFrame:
    """Parses raw rows from Sold_Items worksheet into a typed pandas DataFrame."""
    if not rows or len(rows) < 2:
        return pd.DataFrame()

    headers = [str(h).strip() for h in rows[0]]
    # Deduplicate empty or duplicate header names
    cleaned_headers = []
    seen = {}
    for i, h in enumerate(headers):
        name = h if h else f"Col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cleaned_headers.append(name)

    data_rows = [r for r in rows[1:] if any(str(c).strip() for c in r)]
    if not data_rows:
        return pd.DataFrame()

    df = pd.DataFrame(data_rows, columns=cleaned_headers)

    # Normalize header casing to canonical names
    canonical_aliases = {
        "category": "Category",
        "brand": "Brand",
        "title": "Title",
        "sale_type": "Sale_Type",
        "saletype": "Sale_Type",
        "sale_date": "Sale_Date",
        "saledate": "Sale_Date",
        "date": "Sale_Date",
        "unit_cost": "Unit_Cost",
        "unitcost": "Unit_Cost",
        "posted_price": "Posted_Price",
        "current_price": "Current_Price",
        "sold_price": "Sold_Price",
        "soldprice": "Sold_Price",
        "net_profit": "Net_Profit",
        "netprofit": "Net_Profit",
        "packaging_cost": "Packaging_Cost",
        "margin_%": "Margin_%",
        "margin_pct": "Margin_%",
        "margin": "Margin_%",
    }
    rename_dict = {}
    for col in df.columns:
        norm = col.lower().strip()
        if norm in canonical_aliases and canonical_aliases[norm] not in df.columns:
            rename_dict[col] = canonical_aliases[norm]
    if rename_dict:
        df = df.rename(columns=rename_dict)

    # Clean numeric columns
    numeric_cols = [
        ("Unit_Cost", "unit_cost_num"),
        ("Posted_Price", "posted_price_num"),
        ("Current_Price", "current_price_num"),
        ("Sold_Price", "sold_price_num"),
        ("Price_Discount", "price_discount_num"),
        ("Packaging_Cost", "packaging_cost_num"),
        ("Net_Profit", "net_profit_num"),
    ]
    for orig, target in numeric_cols:
        if orig in df.columns:
            df[target] = df[orig].apply(clean_currency)
        else:
            df[target] = 0.0

    if "Margin_%" in df.columns:
        df["margin_pct_num"] = df["Margin_%"].apply(clean_percent)
    else:
        # Calculate dynamically
        df["margin_pct_num"] = np.where(
            df["sold_price_num"] > 0,
            (df["net_profit_num"] / df["sold_price_num"]) * 100,
            0.0,
        )

    # Clean Date
    if "Sale_Date" in df.columns:
        df["sale_date_dt"] = pd.to_datetime(df["Sale_Date"], errors="coerce")
    else:
        df["sale_date_dt"] = pd.NaT

    # Guarantee canonical columns ALWAYS exist with sensible defaults
    if "Brand" in df.columns:
        df["Brand"] = df["Brand"].fillna("Unbranded").replace("", "Unbranded")
    else:
        df["Brand"] = "Unbranded"

    if "Category" in df.columns:
        df["Category"] = df["Category"].fillna("Other Apparel").replace("", "Other Apparel")
    else:
        df["Category"] = "Other Apparel"

    if "Sale_Type" in df.columns:
        df["Sale_Type"] = df["Sale_Type"].fillna("Original Price").replace("", "Original Price")
    else:
        df["Sale_Type"] = "Original Price"

    if "Title" in df.columns:
        df["Title"] = df["Title"].fillna("Untitled Item").replace("", "Untitled Item")
    else:
        df["Title"] = "Untitled Item"

    return df


def parse_active_dataframe(rows: list) -> pd.DataFrame:
    """Parses raw rows from Item_Listings worksheet into a typed pandas DataFrame."""
    if not rows or len(rows) < 2:
        return pd.DataFrame()

    headers = [str(h).strip() for h in rows[0]]
    cleaned_headers = []
    seen = {}
    for i, h in enumerate(headers):
        name = h if h else f"Col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cleaned_headers.append(name)

    data_rows = [r for r in rows[1:] if any(str(c).strip() for c in r)]
    if not data_rows:
        return pd.DataFrame()

    df = pd.DataFrame(data_rows, columns=cleaned_headers)

    canonical_aliases = {
        "category": "Category",
        "brand": "Brand",
        "title": "Title",
        "unit_cost": "Unit_Cost",
        "posted_price": "Posted_Price",
        "current_price": "Current_Price",
        "favorites": "Favorites",
        "views": "Views",
    }
    rename_dict = {}
    for col in df.columns:
        norm = col.lower().strip()
        if norm in canonical_aliases and canonical_aliases[norm] not in df.columns:
            rename_dict[col] = canonical_aliases[norm]
    if rename_dict:
        df = df.rename(columns=rename_dict)

    numeric_cols = [
        ("Unit_Cost", "unit_cost_num"),
        ("Posted_Price", "posted_price_num"),
        ("Current_Price", "current_price_num"),
    ]
    for orig, target in numeric_cols:
        if orig in df.columns:
            df[target] = df[orig].apply(clean_currency)
        else:
            df[target] = 0.0

    if "Favorites" in df.columns:
        df["favorites_num"] = pd.to_numeric(df["Favorites"], errors="coerce").fillna(0).astype(int)
    else:
        df["favorites_num"] = 0

    if "Views" in df.columns:
        df["views_num"] = pd.to_numeric(df["Views"], errors="coerce").fillna(0).astype(int)
    else:
        df["views_num"] = 0

    if "Brand" in df.columns:
        df["Brand"] = df["Brand"].fillna("Unbranded").replace("", "Unbranded")
    else:
        df["Brand"] = "Unbranded"

    if "Category" in df.columns:
        df["Category"] = df["Category"].fillna("Other Apparel").replace("", "Other Apparel")
    else:
        df["Category"] = "Other Apparel"

    if "Title" in df.columns:
        df["Title"] = df["Title"].fillna("Untitled Item").replace("", "Untitled Item")
    else:
        df["Title"] = "Untitled Item"

    return df


# -----------------------------------------------------------------------------
# Plotly Chart Builders
# -----------------------------------------------------------------------------

def build_sales_trend_chart(df: pd.DataFrame, freq: str = "D") -> go.Figure:
    """Builds a dual-axis timeline chart of sales revenue and net profit."""
    if df.empty or "sale_date_dt" not in df.columns or df["sale_date_dt"].dropna().empty:
        fig = go.Figure()
        fig.add_annotation(text="No sales data available for date range", showarrow=False, font=dict(size=14))
        return fig

    valid_df = df.dropna(subset=["sale_date_dt"]).copy()
    valid_df = valid_df.sort_values("sale_date_dt")

    # Resample by date
    valid_df["period"] = valid_df["sale_date_dt"].dt.to_period(freq).dt.to_timestamp()
    grouped = valid_df.groupby("period").agg(
        revenue=("sold_price_num", "sum"),
        profit=("net_profit_num", "sum"),
        items_sold=("sold_price_num", "count"),
    ).reset_index()

    grouped["cumulative_profit"] = grouped["profit"].cumsum()
    grouped["cumulative_revenue"] = grouped["revenue"].cumsum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Revenue Bar
    fig.add_trace(
        go.Bar(
            x=grouped["period"],
            y=grouped["revenue"],
            name="Gross Sales ($)",
            marker_color="#0284c7",
            opacity=0.85,
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>Sales: $%{y:.2f}<extra></extra>",
        ),
        secondary_y=False,
    )

    # Net Profit Bar
    fig.add_trace(
        go.Bar(
            x=grouped["period"],
            y=grouped["profit"],
            name="Net Profit ($)",
            marker_color="#10b981",
            opacity=0.9,
            hovertemplate="Profit: $%{y:.2f}<extra></extra>",
        ),
        secondary_y=False,
    )

    # Cumulative Profit Line
    fig.add_trace(
        go.Scatter(
            x=grouped["period"],
            y=grouped["cumulative_profit"],
            name="Cumulative Profit ($)",
            mode="lines+markers",
            line=dict(color="#f59e0b", width=3),
            marker=dict(size=6),
            hovertemplate="Cumulative Profit: $%{y:.2f}<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=dict(text="Revenue, Net Profit & Cumulative Growth Over Time", x=0.01),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=50, b=20),
        height=380,
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Period Total ($)", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative Profit ($)", secondary_y=True, showgrid=False)
    return fig


def build_category_chart(df: pd.DataFrame) -> go.Figure:
    """Builds horizontal bar chart comparing net profit & revenue by category."""
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No category data available", showarrow=False)
        return fig

    cat_df = df.groupby("Category").agg(
        revenue=("sold_price_num", "sum"),
        profit=("net_profit_num", "sum"),
        avg_margin=("margin_pct_num", "mean"),
        count=("sold_price_num", "count"),
    ).reset_index().sort_values("profit", ascending=True)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=cat_df["Category"],
            x=cat_df["revenue"],
            name="Gross Sales ($)",
            orientation="h",
            marker_color="#38bdf8",
            hovertemplate="<b>%{y}</b><br>Sales: $%{x:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            y=cat_df["Category"],
            x=cat_df["profit"],
            name="Net Profit ($)",
            orientation="h",
            marker_color="#10b981",
            hovertemplate="Profit: $%{x:.2f}<br>Items: %{customdata[0]}<br>Avg Margin: %{customdata[1]:.1f}%<extra></extra>",
            customdata=cat_df[["count", "avg_margin"]],
        )
    )

    fig.update_layout(
        title=dict(text="Sales & Profit Performance by Category", x=0.01),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=50, b=20),
        height=380,
    )
    fig.update_xaxes(title_text="Amount ($)")
    return fig


def build_top_brands_chart(df: pd.DataFrame, top_n: int = 10) -> go.Figure:
    """Builds a horizontal bar chart of top brands ranked by total net profit."""
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No brand data available", showarrow=False)
        return fig

    brand_df = df.groupby("Brand").agg(
        profit=("net_profit_num", "sum"),
        revenue=("sold_price_num", "sum"),
        items_sold=("sold_price_num", "count"),
        avg_margin=("margin_pct_num", "mean"),
    ).reset_index()

    brand_df = brand_df.sort_values("profit", ascending=False).head(top_n)
    brand_df = brand_df.sort_values("profit", ascending=True)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=brand_df["Brand"],
            x=brand_df["profit"],
            orientation="h",
            marker=dict(
                color=brand_df["profit"],
                colorscale="Viridis",
                showscale=False,
            ),
            hovertemplate="<b>%{y}</b><br>Net Profit: $%{x:.2f}<br>Items Sold: %{customdata[0]}<br>Avg Margin: %{customdata[1]:.1f}%<extra></extra>",
            customdata=brand_df[["items_sold", "avg_margin"]],
        )
    )

    fig.update_layout(
        title=dict(text=f"Top {top_n} Brands by Net Profit", x=0.01),
        margin=dict(l=10, r=10, t=45, b=20),
        height=380,
    )
    fig.update_xaxes(title_text="Net Profit ($)")
    return fig


def build_price_vs_profit_scatter(df: pd.DataFrame) -> go.Figure:
    """Builds an interactive scatter plot of Sold Price vs Net Profit colored by category."""
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No sales data available", showarrow=False)
        return fig

    fig = px.scatter(
        df,
        x="sold_price_num",
        y="net_profit_num",
        color="Category",
        size="margin_pct_num",
        hover_name="Title",
        hover_data={
            "Brand": True,
            "sold_price_num": ":$.2f",
            "net_profit_num": ":$.2f",
            "margin_pct_num": ":.1f%",
            "Sale_Type": True,
        },
        labels={
            "sold_price_num": "Sold Price ($)",
            "net_profit_num": "Net Profit ($)",
            "margin_pct_num": "Margin %",
        },
        title="Price vs Profit Distribution by Item",
    )

    fig.update_layout(
        margin=dict(l=10, r=10, t=45, b=20),
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def build_inventory_split_donuts(df_sold: pd.DataFrame, df_active: pd.DataFrame) -> Tuple[go.Figure, go.Figure]:
    """Builds two donut charts: Sell-Through Status & Active Inventory Value Share."""
    # Chart 1: Sell-Through Ratio
    sold_count = len(df_sold)
    active_count = len(df_active)

    fig1 = go.Figure(
        data=[
            go.Pie(
                labels=["Sold Items", "Active Inventory"],
                values=[sold_count, active_count],
                hole=0.55,
                marker=dict(colors=["#10b981", "#6366f1"]),
                textinfo="label+percent",
            )
        ]
    )
    fig1.update_layout(
        title=dict(text="Catalog Sell-Through Split", x=0.01),
        margin=dict(l=10, r=10, t=40, b=10),
        height=280,
        showlegend=False,
    )

    # Chart 2: Active Inventory Value by Category
    if not df_active.empty and "Category" in df_active.columns:
        cat_val = df_active.groupby("Category")["current_price_num"].sum().reset_index()
        fig2 = go.Figure(
            data=[
                go.Pie(
                    labels=cat_val["Category"],
                    values=cat_val["current_price_num"],
                    hole=0.55,
                    textinfo="percent",
                    hovertemplate="<b>%{label}</b><br>Value: $%{value:.2f}<br>%{percent}<extra></extra>",
                )
            ]
        )
    else:
        fig2 = go.Figure()
        fig2.add_annotation(text="No active inventory", showarrow=False)

    fig2.update_layout(
        title=dict(text="Active Inventory Value Share", x=0.01),
        margin=dict(l=10, r=10, t=40, b=10),
        height=280,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
    )

    return fig1, fig2


def build_sale_types_chart(df: pd.DataFrame) -> go.Figure:
    """Builds a bar chart showing the breakdown of Sale Types (Full Price vs Offers vs Drops)."""
    if df.empty or "Sale_Type" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(text="No sale type data available", showarrow=False)
        return fig

    type_df = df.groupby("Sale_Type").agg(
        count=("sold_price_num", "count"),
        total_profit=("net_profit_num", "sum"),
        avg_price=("sold_price_num", "mean"),
    ).reset_index().sort_values("count", ascending=False)

    fig = go.Figure(
        data=[
            go.Bar(
                x=type_df["Sale_Type"],
                y=type_df["count"],
                marker_color=["#0ea5e9", "#f59e0b", "#8b5cf6", "#ec4899"][: len(type_df)],
                text=type_df["count"],
                textposition="auto",
                hovertemplate="<b>%{x}</b><br>Transactions: %{y}<br>Avg Price: $%{customdata[0]:.2f}<br>Total Profit: $%{customdata[1]:.2f}<extra></extra>",
                customdata=type_df[["avg_price", "total_profit"]],
            )
        ]
    )

    fig.update_layout(
        title=dict(text="Sales by Negotiation Type", x=0.01),
        margin=dict(l=10, r=10, t=45, b=20),
        height=280,
    )
    fig.update_yaxes(title_text="Number of Orders")
    return fig


def compute_net_cash_profit(
    total_revenue: float,
    total_lots_invested: float,
    total_supplies_invested: float,
) -> Dict[str, Any]:
    """Calculates actual net cash profit and cash flow recovery metrics."""
    total_invested = total_lots_invested + total_supplies_invested
    net_cash = total_revenue - total_invested
    is_positive = net_cash >= 0
    cash_roi = ((net_cash / total_invested) * 100) if total_invested > 0 else 0.0
    recovery_pct = ((total_revenue / total_invested) * 100) if total_invested > 0 else (100.0 if total_revenue > 0 else 0.0)

    return {
        "total_revenue": total_revenue,
        "total_lots_invested": total_lots_invested,
        "total_supplies_invested": total_supplies_invested,
        "total_invested": total_invested,
        "actual_net_cash_profit": net_cash,
        "is_positive": is_positive,
        "cash_roi_pct": cash_roi,
        "recovery_pct": recovery_pct,
    }


def build_cash_flow_waterfall(cash_summary: Dict[str, Any]) -> go.Figure:
    """Builds a Plotly waterfall chart visualizing cash outflows (lots + supplies), gross revenue, and net cash profit."""
    rev = cash_summary.get("total_revenue", 0.0)
    lots = cash_summary.get("total_lots_invested", 0.0)
    supplies = cash_summary.get("total_supplies_invested", 0.0)
    net_cash = cash_summary.get("actual_net_cash_profit", 0.0)

    fig = go.Figure(
        go.Waterfall(
            name="Cash Flow",
            orientation="v",
            measure=["relative", "relative", "relative", "total"],
            x=["Gross Revenue", "Inventory Lots Spend", "Supplies Spend", "Actual Net Cash Profit"],
            textposition="outside",
            text=[f"+${rev:,.2f}", f"-${lots:,.2f}", f"-${supplies:,.2f}", f"${net_cash:,.2f}"],
            y=[rev, -lots, -supplies, net_cash],
            connector={"line": {"color": "rgb(63, 63, 63)"}},
            decreasing={"marker": {"color": "#ef4444"}},
            increasing={"marker": {"color": "#10b981"}},
            totals={"marker": {"color": "#0ea5e9" if net_cash >= 0 else "#f59e0b"}},
        )
    )

    fig.update_layout(
        title=dict(text="💵 Cash Flow & Capital Recovery Breakdown", x=0.01),
        showlegend=False,
        height=320,
        margin=dict(l=10, r=10, t=45, b=20),
        yaxis=dict(title="Cash Amount ($)", tickformat="$,.0f"),
    )
    return fig

