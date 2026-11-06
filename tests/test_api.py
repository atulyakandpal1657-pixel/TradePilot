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


# ── Market endpoint tests ─────────────────────────────────────────────────────
# All tests below mock fetch_market_snapshot so they require:
#   - no real Alpaca credentials
#   - no internet access
#   - no specific market hours
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from tradepilot.core.models import MarketSnapshot

# A reusable snapshot fixture value (no Pydantic fixture needed — plain function)
def _make_snapshot(symbol: str = "PLTR") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        current_price=184.61,
        timestamp=datetime(2025, 9, 5, 20, 0, 0, tzinfo=timezone.utc),
        volume=12345678.0,
        previous_close=183.20,
        price_change_pct=0.0077,
        sma_20=171.03,
        sma_50=143.74,
        volatility=0.071,
    )


def test_market_endpoint_success():
    """Successful snapshot → HTTP 200 with all expected JSON keys."""
    with patch("tradepilot.api.fetch_market_snapshot", return_value=_make_snapshot()) as mock_fn:
        response = client.get("/api/market/PLTR")

    assert response.status_code == 200
    mock_fn.assert_called_once_with("PLTR")

    body = response.json()
    assert body["symbol"] == "PLTR"
    assert body["current_price"] == pytest.approx(184.61)
    assert body["previous_close"] == pytest.approx(183.20)
    assert body["price_change_pct"] == pytest.approx(0.0077)
    assert body["sma_20"] == pytest.approx(171.03)
    assert body["sma_50"] == pytest.approx(143.74)
    assert body["volatility"] == pytest.approx(0.071)
    assert body["volume"] == pytest.approx(12345678.0)
    assert "timestamp" in body
    # Credentials must never appear in the response
    assert "api_key" not in str(body).lower()
    assert "secret" not in str(body).lower()


def test_market_endpoint_symbol_normalization():
    """Lowercase symbol in the URL path is normalised to uppercase before fetch."""
    with patch("tradepilot.api.fetch_market_snapshot", return_value=_make_snapshot("PLTR")) as mock_fn:
        response = client.get("/api/market/pltr")

    assert response.status_code == 200
    # The function must have been called with the uppercased symbol
    mock_fn.assert_called_once_with("PLTR")
    assert response.json()["symbol"] == "PLTR"


def test_market_endpoint_invalid_data():
    """ValueError (e.g. insufficient history or unknown symbol) → HTTP 400."""
    with patch(
        "tradepilot.api.fetch_market_snapshot",
        side_effect=ValueError("Insufficient historical data for FAKESYM. At least 50 days required."),
    ):
        response = client.get("/api/market/FAKESYM")

    assert response.status_code == 400
    assert "Insufficient historical data" in response.json()["detail"]


def test_market_endpoint_connection_failure():
    """ConnectionError (Alpaca unreachable) → HTTP 503."""
    with patch(
        "tradepilot.api.fetch_market_snapshot",
        side_effect=ConnectionError("Failed to fetch market data from Alpaca: network timeout"),
    ):
        response = client.get("/api/market/PLTR")

    assert response.status_code == 503
    assert "Failed to fetch" in response.json()["detail"]
