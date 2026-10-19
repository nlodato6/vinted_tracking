from datetime import date
from src.models import InventoryLot, ItemListing, SalesOrder, ShippingSupply


def test_inventory_lot_cost_allocation():
    lot = InventoryLot(
        lot_id="LOT-001",
        purchase_date=date(2026, 10, 15),
        source_vendor="Goodwill Haul",
        total_cost=150.00,
        total_articles=30,
    )
    # 150 / 30 = 5.0
    assert lot.unit_cost == 5.0


def test_inventory_lot_zero_articles():
    lot = InventoryLot(
        lot_id="LOT-002",
        total_cost=50.00,
        total_articles=0,
    )
    assert lot.unit_cost == 0.0


def test_shipping_supply_unit_cost():
    supply = ShippingSupply(
        supply_id="SUP-01",
        item_description="10x13 Poly Mailers",
        quantity=100,
        total_cost=18.50,
    )
    assert supply.unit_cost == 0.185


def test_sales_order_profit_and_margin():
    order = SalesOrder(
        order_id="ORD-1001",
        item_sku="SKU-1001",
        item_title="Vintage Jacket",
        sold_price=60.00,
        net_payout=60.00,
        item_cost=5.00,       # from InventoryLot
        packaging_cost=0.75,  # from ShippingSupply average
    )
    # Net Profit = 60.00 - 5.00 - 0.75 = 54.25
    assert order.net_profit == 54.25
    # Margin % = (54.25 / 60.00) * 100 = 90.42%
    assert order.margin_percent == 90.42


def test_item_listing_favorites_and_views():
    item = ItemListing(
        item_sku="SKU-2001",
        title="Vintage Carhartt Jacket",
        brand="Carhartt",
        category="Outerwear",
        current_price=75.0,
        favorites=18,
        views=120,
    )
    assert item.favorites == 18
    assert item.views == 120
    assert item.brand == "Carhartt"
    assert item.category == "Outerwear"


def test_sales_order_with_favorites_and_category():
    order = SalesOrder(
        order_id="ORD-2001",
        item_sku="SKU-2001",
        item_title="Vintage Carhartt Jacket",
        brand="Carhartt",
        category="Outerwear",
        favorites=18,
        sold_price=70.0,
        net_payout=70.0,
        item_cost=10.0,
        packaging_cost=0.75,
    )
    assert order.favorites == 18
    assert order.brand == "Carhartt"
    assert order.category == "Outerwear"
    assert order.net_profit == 59.25
