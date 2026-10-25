import pytest
from src.html_parser import (
    parse_closet_html,
    parse_orders_html,
    parse_orders_and_cancellations,
    detect_html_type,
    infer_clothing_category,
    infer_brand,
    extract_vinted_id,
    parse_price,
)


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


def test_parse_closet_html_hidden_and_reserved_badges():
    sample_html = """
    <div class="feed-grid__item">
        <a href="/items/11111111-bikini" title="Olive Bikini">
            <span class="badge">Hidden</span>
            <div>$12.00</div>
        </a>
    </div>
    <div class="feed-grid__item">
        <a href="/items/22222222-coat" title="Winter Coat">
            <span class="badge">Reserved</span>
            <div>$40.00</div>
        </a>
    </div>
    <div class="feed-grid__item">
        <a href="/items/33333333-shirt" title="Graphic Tee">
            <span class="badge">Sold</span>
            <div>$15.00</div>
        </a>
    </div>
    <div class="feed-grid__item">
        <a href="/items/44444444-jeans" title="Blue Jeans">
            <div>$20.00</div>
        </a>
    </div>
    """
    items = parse_closet_html(sample_html)
    assert len(items) == 4
    statuses = {i.title: i.status for i in items}
    assert statuses["Olive Bikini"] == "Hidden"
    assert statuses["Winter Coat"] == "Reserved"
    assert statuses["Graphic Tee"] == "Sold"
    assert statuses["Blue Jeans"] == "Listed"


def test_parse_closet_spa_bleed_prevention():
    sample_html = """
    <html>
    <head><title>user - member profile | Sold items | Vinted</title></head>
    <body>
    <div class="feed-grid__item">
        <a href="/items/101-sold-boots" title="Leather Boots">
            <span class="badge">Sold</span>
            <div>$35.00</div>
        </a>
    </div>
    <div class="feed-grid__item">
        <a href="/items/102-active-hat" title="Wool Hat">
            <div>$10.00</div>
        </a>
    </div>
    </body>
    </html>
    """
    items_filtered = parse_closet_html(sample_html, filter_sold_only=True)
    assert len(items_filtered) == 1
    assert items_filtered[0].title == "Leather Boots"
    assert items_filtered[0].status == "Sold"

    items_auto = parse_closet_html(sample_html)
    assert len(items_auto) == 1
    assert items_auto[0].title == "Leather Boots"


def test_parse_orders_html_cancellations_and_bundles():
    sample_html = """
    <a class="web_ui__Cell__link" data-testid="my-orders-item" href="https://www.vinted.com/inbox/order-c1">
        <div>
            <span>Transaction status: Failed</span>
            <span>Joe's Jeans</span>
            <span>$3.40</span>
            <span>Canceled. Refund processed.</span>
        </div>
    </a>
    <a class="web_ui__Cell__link" data-testid="my-orders-item" href="https://www.vinted.com/inbox/order-c2">
        <div>
            <span>Transaction status: Failed</span>
            <span>Bundle 5 items</span>
            <span>$15.00</span>
            <span>Canceled. Refund processed.</span>
        </div>
    </a>
    <a class="web_ui__Cell__link" data-testid="my-orders-item" href="https://www.vinted.com/inbox/order-ok1">
        <div>
            <span>Transaction status: Completed</span>
            <span>Bundle 2 items</span>
            <span>$8.50</span>
            <span>Order completed successfully.</span>
        </div>
    </a>
    <a class="web_ui__Cell__link" data-testid="my-orders-item" href="https://www.vinted.com/inbox/order-ok2">
        <div>
            <span>Transaction status: Actions required</span>
            <span>21 Pack of Boxer Briefs (21)</span>
            <span>$16.00</span>
            <span>Shipping label sent to seller.</span>
        </div>
    </a>
    """
    sales, cancelled = parse_orders_and_cancellations(sample_html)
    assert len(sales) == 2
    assert len(cancelled) == 2

    assert cancelled[0].order_id == "order-c1"
    assert cancelled[0].vinted_status == "Cancelled"
    assert cancelled[0].cancellation_reason == "Canceled. Refund processed."
    assert cancelled[0].quantity == 1

    assert cancelled[1].order_id == "order-c2"
    assert cancelled[1].vinted_status == "Cancelled"
    assert cancelled[1].quantity == 5

    bundle_sale = [s for s in sales if s.order_id == "order-ok1"][0]
    assert bundle_sale.vinted_status == "Completed"
    assert bundle_sale.quantity == 2
    assert bundle_sale.sold_price == 8.50

    pack_sale = [s for s in sales if s.order_id == "order-ok2"][0]
    assert pack_sale.vinted_status == "In Progress"
    assert pack_sale.quantity == 21
    assert pack_sale.sold_price == 16.00

    only_valid = parse_orders_html(sample_html, filter_cancelled=True)
    assert len(only_valid) == 2
    assert all(o.vinted_status != "Cancelled" for o in only_valid)


