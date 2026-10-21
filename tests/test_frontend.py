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


def test_invintory_assets_and_branding():
    from pathlib import Path

    logo_path = Path("assets/invintory.png")
    assert logo_path.exists(), "assets/invintory.png must exist"
    assert logo_path.stat().st_size > 1000, "assets/invintory.png should be non-empty"

    app_py = Path("app.py").read_text()
    assert "InVintory" in app_py
    assert "From inbox alerts to bottom-line gains." in app_py

    metrics_py = Path("app_pages/metrics.py").read_text()
    assert "InVintory" in metrics_py
    assert "From inbox alerts to bottom-line gains." in metrics_py

    sync_py = Path("app_pages/sync.py").read_text()
    assert "InVintory" in sync_py
    assert "From inbox alerts to bottom-line gains." in sync_py

