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


def test_normalize_date_str():
    from src.sheets.sync import normalize_date_str

    assert normalize_date_str("2026-10-19") == "2026-10-19"
    assert normalize_date_str("10/19/2026") == "2026-10-19"
    assert normalize_date_str("2026/10/19") == "2026-10-19"
    assert normalize_date_str("10-19-2026") == "2026-10-19"
    assert normalize_date_str("") == ""


def test_parse_and_save_cookies_json(tmp_path, monkeypatch):
    import json
    from src.scraper.auth import parse_and_save_cookies
    from src.config import settings

    test_file = tmp_path / "vinted_state.json"
    monkeypatch.setattr(settings, "session_dir", str(tmp_path))

    json_payload = json.dumps([
        {"name": "v_sid", "value": "test_session_123", "domain": "vinted.com"},
        {"name": "access_token_web", "value": "test_token_456", "domain": ".vinted.com"}
    ])

    saved_path = parse_and_save_cookies(json_payload)
    assert saved_path.exists()
    saved_data = json.loads(saved_path.read_text())
    assert "cookies" in saved_data
    assert len(saved_data["cookies"]) == 2
    assert saved_data["cookies"][0]["name"] == "v_sid"
    assert saved_data["cookies"][0]["domain"] == ".vinted.com"


def test_parse_and_save_cookies_header_string(tmp_path, monkeypatch):
    import json
    from src.scraper.auth import parse_and_save_cookies
    from src.config import settings

    monkeypatch.setattr(settings, "session_dir", str(tmp_path))

    header_payload = "v_sid=abc123; access_token_web=xyz789; anon_id=anon_456"
    saved_path = parse_and_save_cookies(header_payload)
    assert saved_path.exists()
    saved_data = json.loads(saved_path.read_text())
    assert len(saved_data["cookies"]) == 3
    names = {c["name"] for c in saved_data["cookies"]}
    assert names == {"v_sid", "access_token_web", "anon_id"}

