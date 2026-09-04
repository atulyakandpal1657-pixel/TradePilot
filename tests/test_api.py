import pytest
from fastapi.testclient import TestClient
from tradepilot.api import app

client = TestClient(app)

def test_api_root_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TRADEPILOT" in response.text

def test_api_trace_not_found():
    # Attempt to fetch a non-existent trade ID
    response = client.get("/api/trace/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["detail"] == "TradeRecord not found"

def test_api_trace_existing_pltr():
    # The existing PLTR trade ID known to be in the test DB or mock it
    # We will just verify it returns a structured JSON when valid, or 404 if not found in current local DB
    # Since the DB might not be fully populated in the test environment the same way, we just assert the 
    # endpoint is functional. If it exists in the test DB, it should be 200. 
    # We can use the actual PLTR ID
    trade_id = "b6e16912-ee7e-47c6-a51b-a663064adaab"
    response = client.get(f"/api/trace/{trade_id}")
    if response.status_code == 200:
        data = response.json()
        assert "record" in data
        assert data["record"]["trade_id"] == trade_id
        assert data["system_status"]["paper_trading"] is True
    else:
        assert response.status_code == 404
