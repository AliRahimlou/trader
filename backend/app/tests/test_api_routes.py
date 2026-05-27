from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_start_paper_bot_and_fetch_status():
    response = client.post(
        "/bot/start",
        json={
            "mode": "paper",
            "auto_pick": True,
            "capital": 10000,
            "risk_level": "balanced",
            "strategy": "auto_strategy",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"]["mode"] == "paper"
    assert payload["session"]["status"] in {"SCANNING", "WAITING", "IN POSITION"}
    status_response = client.get(f"/bot/{payload['session']['id']}/status")
    assert status_response.status_code == 200


def test_market_rankings_returns_required_candidate_shape():
    response = client.get("/market/rankings?auto_pick=true&strategy=auto_strategy&risk_level=balanced&capital=10000")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload
    assert {"ticker", "confidence", "entry_trigger", "stop_loss", "take_profit", "allowed"}.issubset(payload[0].keys())
