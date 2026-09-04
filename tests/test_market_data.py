import pytest
from datetime import datetime, timezone
from tradepilot.core.models import MarketSnapshot
from tradepilot.alpaca.market_data import fetch_market_snapshot
from tradepilot.core.config import settings

def has_alpaca_credentials():
    return bool(settings.alpaca_api_key and settings.alpaca_secret_key)

@pytest.mark.skipif(not has_alpaca_credentials(), reason="Alpaca credentials missing.")
def test_fetch_market_snapshot_integration():
    """Test fetching data for a highly liquid stock like AAPL"""
    snapshot = fetch_market_snapshot("AAPL")
    
    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.symbol == "AAPL"
    assert snapshot.current_price > 0
    assert snapshot.sma_20 > 0
    assert snapshot.sma_50 > 0
    assert snapshot.volume >= 0
    assert snapshot.previous_close > 0
    
def test_invalid_symbol(monkeypatch):
    """Test invalid symbol handling, assuming credentials exist. Let's just mock the client."""
    from unittest.mock import MagicMock
    
    class MockClient:
        def get_stock_bars(self, req):
            mock_bars = MagicMock()
            mock_bars.data = {}
            return mock_bars
            
    monkeypatch.setattr("tradepilot.alpaca.market_data.get_market_data_client", lambda: MockClient())
    
    with pytest.raises(ValueError, match="Insufficient historical data"):
        fetch_market_snapshot("INVALID_TICKER")

def test_missing_credentials(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "")
    with pytest.raises(ValueError, match="Alpaca credentials are not configured"):
        fetch_market_snapshot("AAPL")
