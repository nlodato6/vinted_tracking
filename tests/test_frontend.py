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


def test_spreadthrift_assets_and_branding():
    from pathlib import Path

    logo_path = Path("assets/invintory.png")
    assert logo_path.exists(), "assets/invintory.png must exist"
    assert logo_path.stat().st_size > 1000, "assets/invintory.png should be non-empty"

    app_py = Path("app.py").read_text()
    assert "SpreadThrift" in app_py
    assert "From inbox alerts to bottom-line gains." in app_py
    assert "Third-Party Trademark & Legal Disclaimer" in app_py

    metrics_py = Path("app_pages/metrics.py").read_text()
    assert "SpreadThrift" in metrics_py
    assert "From inbox alerts to bottom-line gains." in metrics_py

    sync_py = Path("app_pages/sync.py").read_text()
    assert "SpreadThrift" in sync_py
    assert "From inbox alerts to bottom-line gains." in sync_py

    readme_md = Path("README.md").read_text()
    assert "SpreadThrift" in readme_md
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
    assert "Assigned Lot (Editable)" in sync_code or '"Assigned Lot": lot_column_config' in sync_code
    assert "auto_assign_lot=False" in sync_code


