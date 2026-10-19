from src.scraper.closet import parse_price, extract_vinted_id


def test_parse_price():
    assert parse_price("$45.00") == 45.0
    assert parse_price("$45") == 45.0
    assert parse_price("45,50 €") == 45.5
    assert parse_price("$ 120.99") == 120.99
    assert parse_price("") == 0.0
    assert parse_price("N/A") == 0.0


def test_extract_vinted_id():
    url1 = "https://www.vinted.com/items/492019482-vintage-levis-jeans"
    assert extract_vinted_id(url1) == "492019482"

    url2 = "/items/12345678"
    assert extract_vinted_id(url2) == "12345678"

    text = "item-id-998877"
    assert extract_vinted_id(text) == "998877"


def test_infer_clothing_category():
    from src.scraper.closet import infer_clothing_category

    assert infer_clothing_category("Vintage Carhartt Canvas Jacket") == "Outerwear"
    assert infer_clothing_category("Retro Nike Puffer Vest") == "Outerwear"
    assert infer_clothing_category("Stussy Embroidered Hoodie") == "Sweaters & Hoodies"
    assert infer_clothing_category("Ralph Lauren Knit Crewneck Sweater") == "Sweaters & Hoodies"
    assert infer_clothing_category("Vintage Band Graphic T-Shirt") == "Tops & Shirts"
    assert infer_clothing_category("Levi's 501 Straight Leg Denim Jeans") == "Pants & Jeans"
    assert infer_clothing_category("Nike Dunk Low Retro Sneakers") == "Footwear"
    assert infer_clothing_category("Vintage Corduroy Strapback Cap") == "Accessories"
    assert infer_clothing_category("Floral Summer Midi Dress") == "Dresses & Skirts"
    assert infer_clothing_category("Unknown Item") == "Other Apparel"


def test_infer_brand():
    from src.scraper.closet import infer_brand

    assert infer_brand("Vintage Carhartt Detroit Jacket", "") == "Carhartt"
    assert infer_brand("Retro Windbreaker", "Brand: Nike, Size L") == "Nike"
    assert infer_brand("Vintage 505 Regular Fit Jeans", "Levis red tab") == "Levi's"
    assert infer_brand("The North Face Denali Fleece", "") == "The North Face"
    assert infer_brand("Random Generic Shirt", "No brand") == ""
