import pytest
from src.html_parser import parse_closet_html, parse_orders_html, infer_clothing_category, infer_brand, extract_vinted_id, parse_price


def test_infer_clothing_category():
    assert infer_clothing_category("Vintage Denim Jacket") == "Outerwear"
    assert infer_clothing_category("Oversized Grey Hoodie") == "Sweaters & Hoodies"
    assert infer_clothing_category("Graphic Band Tee") == "Tops & Shirts"
    assert infer_clothing_category("Levi 501 Blue Jeans") == "Pants & Jeans"
    assert infer_clothing_category("Nike Air Jordan 1 Shoes") == "Footwear"
    assert infer_clothing_category("Leather Crossbody Bag") == "Accessories"
    assert infer_clothing_category("Floral Maxi Dress") == "Dresses & Skirts"
    assert infer_clothing_category("Random Object") == "Other Apparel"


def test_infer_brand():
    assert infer_brand("Levi 501 jeans") == "Levi's"
    assert infer_brand("Vintage Carhartt Work Jacket") == "Carhartt"
    assert infer_brand("Black Hoodie", "Nike Air") == "Nike"
    assert infer_brand("Unbranded Tee") == ""


def test_extract_vinted_id():
    assert extract_vinted_id("https://www.vinted.com/items/10208001195-empire-waist-mini-dress") == "10208001195"
    assert extract_vinted_id("/items/987654321") == "987654321"


def test_parse_price():
    assert parse_price("$14.50") == 14.50
    assert parse_price("20.00 €") == 20.00
    assert parse_price(18) == 18.0
    assert parse_price(None) == 0.0


def test_parse_closet_html():
    sample_html = """
    <div class="feed-grid__item">
        <a href="/items/12345678-vintage-nike-tee" title="Vintage Nike Tee">
            <img alt="Vintage Nike Tee" />
            <div class="web_ui__Text__subtitle">$25.00</div>
            <span>3 favourites</span>
        </a>
    </div>
    <div class="feed-grid__item">
        <a href="/items/87654321-carhartt-jacket" title="Carhartt Jacket">
            <div class="web_ui__Text__subtitle">$45.00</div>
            <span>Sold</span>
        </a>
    </div>
    """
    items = parse_closet_html(sample_html, default_lot="LOT-001")
    assert len(items) == 2
    assert items[0].title == "Vintage Nike Tee"
    assert items[0].vinted_id == "12345678"
    assert items[0].posted_price == 25.0
    assert items[0].favorites == 3
    assert items[0].status == "Listed"
    assert items[0].lot_id == "LOT-001"

    assert items[1].title == "Carhartt Jacket"
    assert items[1].status == "Sold"


def test_parse_orders_html():
    sample_orders_html = """
    <div class="order-card">
        <a href="/transaction/ORD-998877">Transaction ORD-998877</a>
        <a href="/items/554433-blue-polo">Blue Polo</a>
        <span>Total: $18.00</span>
    </div>
    """
    orders = parse_orders_html(sample_orders_html)
    assert len(orders) == 1
    assert orders[0].order_id == "ORD-998877"
    assert orders[0].item_title == "Blue Polo"
    assert orders[0].sold_price == 18.0


def test_parse_closet_vinted_real_format():
    sample_real = """
    <div class="feed-grid__item" data-testid="grid-item">
        <div data-testid="product-item-id-10452369337">
            <a href="https://www.vinted.com/items/10452369337" title="Black Skinny Jeans, brand: Madewell, condition: Very good, size: S / US 4-6, $4.00">
                <img alt="Black Skinny Jeans, brand: Madewell, condition: Very good, size: S / US 4-6, $4.00" />
            </a>
            <div data-testid="product-item-id-10452369337--description-content">
                <p>12 views</p>
                <p>4 favorites</p>
            </div>
            <p data-testid="product-item-id-10452369337--price-text">$4.00</p>
        </div>
    </div>
    """
    items = parse_closet_html(sample_real, default_lot="LOT-001")
    assert len(items) == 1
    assert items[0].title == "Black Skinny Jeans"
    assert items[0].brand == "Madewell"
    assert items[0].posted_price == 4.0
    assert items[0].vinted_id == "10452369337"
    assert items[0].favorites == 4
    assert items[0].views == 12
    assert items[0].category == "Pants & Jeans"


def test_parse_orders_vinted_real_format():
    sample_orders = """
    <a class="web_ui__Cell__link" data-testid="my-orders-item" href="https://www.vinted.com/inbox/01a156e8-4d82-7f71-a896-cfb3f421c16c">
        <div>
            <span>Transaction status: Actions required</span>
            <span>Wrangler Regular Taper Cargo Pants</span>
            <span>$6.00</span>
            <span>Shipping label sent to seller.</span>
        </div>
    </a>
    """
    orders = parse_orders_html(sample_orders)
    assert len(orders) == 1
    assert orders[0].order_id == "01a156e8-4d82-7f71-a896-cfb3f421c16c"
    assert orders[0].item_title == "Wrangler Regular Taper Cargo Pants"
    assert orders[0].sold_price == 6.0
    assert orders[0].brand == "Wrangler"
    assert orders[0].category == "Pants & Jeans"

