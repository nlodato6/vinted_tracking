from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from src.server import app

client = TestClient(app)


@patch("src.server.verify_session", new_callable=AsyncMock)
def test_health_check(mock_verify):
    mock_verify.return_value = True
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "vinted_session_active" in data


def test_sync_trigger_invalid_type():
    response = client.post("/sync", json={"sync_type": "invalid"})
    assert response.status_code == 400


@patch("src.server.run_sync_task", new_callable=AsyncMock)
def test_sync_trigger_valid(mock_sync):
    response = client.post("/sync", json={"sync_type": "listings"})
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


@patch("src.server.run_sync_task", new_callable=AsyncMock)
def test_email_webhook_sale_detection(mock_sync):
    payload = {
        "subject": "You've made a sale: Vintage Jacket",
        "body": "Congratulations on your sale! The buyer has paid.",
    }
    response = client.post("/webhook/vinted-email", json=payload)
    assert response.status_code == 200
    assert response.json()["event"] == "sale_detected"
    assert response.json()["action"] == "sync_queued"


def test_email_webhook_non_sale():
    payload = {
        "subject": "Weekly Newsletter",
        "body": "Here are trending fashion pieces.",
    }
    response = client.post("/webhook/vinted-email", json=payload)
    assert response.status_code == 200
    assert response.json()["event"] == "non_sale_email"
