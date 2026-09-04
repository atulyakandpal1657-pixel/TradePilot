import pytest
from tradepilot.core.config import settings
from tradepilot.alpaca.portfolio import fetch_portfolio_state
from tradepilot.core.models import PortfolioState
import os

# Helper to check if we can run integration tests
def has_alpaca_credentials():
    return bool(settings.alpaca_api_key and settings.alpaca_secret_key)

@pytest.mark.skipif(not has_alpaca_credentials(), reason="Alpaca credentials missing.")
def test_fetch_portfolio_state_integration():
    """
    Tests that we can successfully query the paper account and map it to our model.
    """
    # Ensure paper trading is enforced
    assert settings.alpaca_paper_trade is True, "Must be paper trading"
    
    try:
        portfolio = fetch_portfolio_state()
        assert isinstance(portfolio, PortfolioState)
        assert portfolio.account_equity >= 0
        assert portfolio.buying_power >= 0
        assert portfolio.cash >= 0
        assert portfolio.number_of_open_positions == len(portfolio.open_positions)
        assert len(portfolio.position_quantities) == portfolio.number_of_open_positions
    except Exception as e:
        pytest.fail(f"Integration test failed: {e}")

def test_fetch_portfolio_state_missing_creds(monkeypatch):
    """
    Tests error handling when credentials are missing.
    """
    monkeypatch.setattr(settings, "alpaca_api_key", "")
    with pytest.raises(ValueError, match="Alpaca credentials are not configured"):
        fetch_portfolio_state()

def test_client_enforces_paper_trade(monkeypatch):
    """
    Tests that the client outright rejects LIVE trading configurations.
    """
    monkeypatch.setattr(settings, "alpaca_api_key", "test_key")
    monkeypatch.setattr(settings, "alpaca_secret_key", "test_secret")
    monkeypatch.setattr(settings, "alpaca_paper_trade", False)
    
    with pytest.raises(ValueError, match="LIVE trading is prohibited"):
        fetch_portfolio_state()
