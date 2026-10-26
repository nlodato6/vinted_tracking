"""Tests for frontend helpers, HTML type detection, and OAuth integration."""

from src.html_parser import detect_html_type
from src.auth import is_oauth_configured, get_oauth_config


def test_detect_html_type_orders():
    sample_orders = '<a data-testid="my-orders-item" href="/inbox/01a156e8-4d82">Order</a>'
    assert detect_html_type(sample_orders) == "orders"


def test_detect_html_type_active_closet():
    sample_active = '<div data-testid="product-item-id-10452369337"><button>Bump</button></div>'
    assert detect_html_type(sample_active) == "active_closet"


def test_detect_html_type_sold_closet():
    sample_sold = '<!-- member profile _ Vinted_sold --><div data-testid="product-item-id-10452369337"><span>Sold</span></div>'
    assert detect_html_type(sample_sold) == "sold_closet"


def test_detect_html_type_unknown():
    sample_unknown = '<html><body><h1>Hello World</h1></body></html>'
    assert detect_html_type(sample_unknown) == "unknown"


def test_is_oauth_configured():
    # In test environment, returns boolean without crashing
    assert isinstance(is_oauth_configured(), bool)


def test_thread_count_assets_and_branding():
    from pathlib import Path

    logo_path = Path("assets/invintory.png")
    assert logo_path.exists(), "assets/invintory.png must exist"
    assert logo_path.stat().st_size > 1000, "assets/invintory.png should be non-empty"

    app_py = Path("app.py").read_text()
    assert "Thread Count" in app_py
    assert "From inbox alerts to bottom-line gains." in app_py
    assert "Legal & Compliance" in app_py

    metrics_py = Path("app_pages/metrics.py").read_text()
    assert "Thread Count" in metrics_py
    assert "From inbox alerts to bottom-line gains." in metrics_py

    sync_py = Path("app_pages/sync.py").read_text()
    assert "Thread Count" in sync_py
    assert "From inbox alerts to bottom-line gains." in sync_py

    readme_md = Path("README.md").read_text()
    assert "Thread Count" in readme_md
    assert "Third-Party Trademarks & Legal Disclaimer" in readme_md


def test_sync_page_html_parser_data_editor_and_lot_options():
    from pathlib import Path
    sync_code = Path("app_pages/sync.py").read_text()

    # Verify optional unassigned lot selection
    assert "None / No Lot (Unassigned)" in sync_code
    assert "is_none_lot" in sync_code

    # Verify interactive st.data_editor replaces static dataframe
    assert "st.data_editor(" in sync_code
    assert "html_new_listings_editor" in sync_code
    assert '"Assigned Lot": st.column_config.SelectboxColumn(' in sync_code
    assert '"Vinted ID": st.column_config.TextColumn(' in sync_code
    # HTML import is plan-based: preview and commit share the same reconciler
    assert "build_sync_plan(" in sync_code
    assert "apply_sync_plan(" in sync_code
    assert "listing_overrides=overrides" in sync_code


def test_cloud_ready_auth_and_manual_login():
    from pathlib import Path
    from src.config import settings

    # 1. Verify admin configuration
    assert "nicashlod@gmail.com" in settings.admin_email_list
    assert "nicashlod@gmail" in settings.admin_email_list
    assert settings.fallback_admin_password == "PassWord123!@#"

    # 2. Verify local login is removed from app.py
    app_code = Path("app.py").read_text()
    assert "Continue with Local Credentials" not in app_code
    assert "Local Mode (Personal Workspace)" not in app_code

    # 3. Verify manual fallback login exists
    assert "Manual Login (Fallback)" in app_code
    assert "PassWord123!@#" in app_code or "settings.fallback_admin_password" in app_code
    assert "manual_login_email" in app_code
    assert 'placeholder="user@threadcount.com"' in app_code or 'placeholder="user@sheetcount.com"' in app_code


def test_format_sync_timestamp():
    from app_pages.sync import format_sync_timestamp
    from datetime import datetime, timedelta

    # 1. Unrun or empty
    disp, rel = format_sync_timestamp("Not yet run")
    assert disp == "Not yet run"
    assert rel == "Awaiting sync"

    disp_none, rel_none = format_sync_timestamp(None)
    assert disp_none == "Not yet run"
    assert rel_none == "Awaiting sync"

    # 2. Today
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    disp_today, rel_today = format_sync_timestamp(now_str)
    assert "Today," in disp_today
    assert ("ago" in rel_today or "Just now" in rel_today)

    # 3. Yesterday
    yesterday = now - timedelta(days=1)
    disp_yest, _ = format_sync_timestamp(yesterday.strftime("%Y-%m-%d %H:%M"))
    assert "Yesterday," in disp_yest

    # 4. Older date
    past = now - timedelta(days=10)
    disp_past, _ = format_sync_timestamp(past.strftime("%Y-%m-%d %H:%M"))
    assert past.strftime("%b") in disp_past


def test_sync_metrics_styling_and_config():
    from pathlib import Path

    # Verify config.toml base is dark and metricValueFontSize is set to 1.5rem
    config_toml = Path(".streamlit/config.toml").read_text()
    assert 'base = "dark"' in config_toml
    assert 'metricValueFontSize = "1.5rem"' in config_toml

    # Verify sync.py implements render_sync_kpi_cards and custom styling options
    sync_code = Path("app_pages/sync.py").read_text()
    assert "render_sync_kpi_cards" in sync_code
    assert "format_sync_timestamp" in sync_code
    assert "sync-metrics-grid" in sync_code
    assert "sync-metric-val-date" in sync_code
    assert "Deep Slate (Dark)" in sync_code
    assert "Vibrant Dark" in sync_code
    assert "Minimalist Dark" in sync_code
    assert "Custom Colors" in sync_code
    assert "color_picker" in sync_code


def test_cancellation_ui_and_bookmarklet_integration():
    from pathlib import Path

    # Verify metrics.py has cancellation metrics and charts
    metrics_code = Path("app_pages/metrics.py").read_text()
    assert "parse_cancelled_dataframe" in metrics_code
    assert "Cancellation Rate" in metrics_code
    assert "Lost Gross Revenue" in metrics_code
    assert "Cancelled Units" in metrics_code
    assert "Hidden Vault Items" in metrics_code
    assert "Cancellation Intelligence & Lost Revenue" in metrics_code
    assert "build_cancellation_reasons_chart" in metrics_code
    assert "build_cancellation_timeline_chart" in metrics_code

    # Verify sync.py has bookmarklet, date range filter, and snapshot-based reconciliation
    sync_code = Path("app_pages/sync.py").read_text()
    assert "1-Click Auto-Scroll Bookmarklet" in sync_code
    assert "build_snapshot" in sync_code
    assert "build_sync_plan" in sync_code
    assert "reconcile_active_closet_snapshot" not in sync_code
    assert "get_last_scan_date" in sync_code
    assert "Date Range to Scan" in sync_code
    assert "Since Last Scan" in sync_code





