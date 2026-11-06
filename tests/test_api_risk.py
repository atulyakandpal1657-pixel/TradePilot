import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from tradepilot.api import app
from tradepilot.core.models import (
    MarketSnapshot, PortfolioState, StrategyProposal, Action, RiskStatus
)
from tradepilot.core.config import settings

client = TestClient(app)

@pytest.fixture
def mock_snapshot():
    return MarketSnapshot(
        symbol="AAPL",
        current_price=150.0,
        timestamp=datetime(2023, 10, 1, 12, 0, tzinfo=timezone.utc),
        volume=1000000.0,
        previous_close=148.0,
        price_change_pct=0.0135,
        sma_20=145.0,
        sma_50=140.0,
        volatility=0.02
    )

@pytest.fixture
def mock_portfolio():
    return PortfolioState(
        account_equity=10000.0,
        buying_power=20000.0,
        cash=10000.0,
        current_daily_loss=0.0,
        open_positions=["MSFT"],
        number_of_open_positions=1,
        position_quantities={"MSFT": 50.0}
    )

@pytest.fixture
def valid_strategy_proposal():
    return {
        "action": "BUY",
        "confidence": 0.8,
        "thesis": "Strong momentum",
        "evidence": "Price > SMA20",
        "assumptions": ["Trend continues"],
        "invalidation_conditions": ["Price drops below 140"],
        "suggested_stop_loss": 140.0,
        "reasoning_summary": "Buy AAPL",
        "memory_influence": "NONE",
        "memory_reasoning": ""
    }

def test_evaluate_risk_buy_approved(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["action"] == "BUY"
        assert data["risk_status"] == RiskStatus.APPROVED.value
        assert "All risk parameters met" in data["risk_reasons"][-1]
        assert data["quantity"] == 6.0 # (10000 * 0.10) / 150 = 6 shares
        assert "Human approval required" in data["message_human"]

def test_evaluate_risk_stale_analysis(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot):
        stale_timestamp = datetime(2023, 10, 1, 11, 0, tzinfo=timezone.utc)
        req_body = {
            "snapshot_timestamp": stale_timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 409
        data = response.json()
        assert "Stale analysis" in data["detail"]

def test_evaluate_risk_buy_rejected_max_position_pct(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    # Construct an intentionally invalid proposal scenario by mocking the sizing helper
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.calculate_position_size", return_value=1000.0): # 1000 * 150 = 150,000 > 10%
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("Position exposure" in r and "exceeds max allowed" in r for r in data["risk_reasons"])

def test_evaluate_risk_buy_rejected_insufficient_buying_power(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    mock_portfolio.buying_power = 100.0 # Intentionally low buying power
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("Insufficient buying power" in r for r in data["risk_reasons"])

def test_evaluate_risk_buy_rejected_max_open_positions(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    mock_portfolio.number_of_open_positions = 5 # Hits the limit of 5
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("Max open positions reached" in r for r in data["risk_reasons"])

def test_evaluate_risk_buy_rejected_daily_loss(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    mock_portfolio.current_daily_loss = 2500.0 # Limit is 2000.0
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("Daily loss limit reached" in r for r in data["risk_reasons"])

def test_evaluate_risk_buy_rejected_stop_loss_risk(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    valid_strategy_proposal["suggested_stop_loss"] = 50.0 # Risk = $100 * 6 = $600 < $10000 max risk... wait.
    # To exceed 10,000 max risk with 6 shares, we'd need risk > 10,000 / 6 = 1666, but price is 150.
    # We should mock calculate_position_size to return a larger quantity, say 1000 shares.
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.calculate_position_size", return_value=1000.0): # 1000 shares. Risk = 100 * 1000 = $100,000
        valid_strategy_proposal["suggested_stop_loss"] = 50.0 
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("Trade risk" in r and "exceeds max allowed" in r for r in data["risk_reasons"])

def test_evaluate_risk_sell_to_close(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    valid_strategy_proposal["action"] = "SELL"
    valid_strategy_proposal["suggested_stop_loss"] = 160.0
    mock_snapshot.symbol = "MSFT"
    
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/MSFT", json=req_body)
        assert response.status_code == 200
        data = response.json()
        # Since size is calculated by 10% of equity (6 shares), we are selling 6 shares of MSFT.
        # We hold 50 shares. So this SELL should be APPROVED.
        assert data["risk_status"] == RiskStatus.APPROVED.value

def test_evaluate_risk_sell_rejected_no_position(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    valid_strategy_proposal["action"] = "SELL"
    valid_strategy_proposal["suggested_stop_loss"] = 160.0
    # AAPL is not held
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == RiskStatus.REJECTED.value
        assert any("no long position" in r for r in data["risk_reasons"])

def test_evaluate_risk_no_trade(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    valid_strategy_proposal["action"] = "NO_TRADE"
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_status"] == "NOT_APPLICABLE"
        assert data["quantity"] == 0

def test_evaluate_risk_ai_unavailable_handled_by_frontend():
    # If AI is unavailable, frontend won't have a proposal to send, or the analysis endpoint fails.
    # We test that bad Pydantic validation handles tampered inputs.
    pass

def test_tampered_frontend_proposal(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    valid_strategy_proposal["action"] = "INVALID_ACTION" # Tampered action
    
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 422 # FastAPI Pydantic validation error

def test_no_execution_calls(mock_snapshot, mock_portfolio, valid_strategy_proposal):
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.memory.journal.TradeJournal.save_record") as mock_save_record, \
         patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute") as mock_execute:
        
        req_body = {
            "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
            "strategy_proposal": valid_strategy_proposal
        }
        response = client.post("/api/risk/AAPL", json=req_body)
        assert response.status_code == 200
        
        # Verify no execution or execution journaling occurred
        mock_save_record.assert_not_called()
        mock_execute.assert_not_called()
