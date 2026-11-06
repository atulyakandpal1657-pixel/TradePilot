"""
Tests for POST /api/analyze/{symbol}
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from tradepilot.api import app
from tradepilot.core.models import (
    MarketSnapshot,
    PortfolioState,
    StrategyProposal,
    Action,
    MemoryInfluence,
)

client = TestClient(app)

FIXED_TS = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

@pytest.fixture
def mock_snapshot():
    return MarketSnapshot(
        symbol="PLTR",
        current_price=25.50,
        timestamp=FIXED_TS,
        volume=5_000_000,
        previous_close=27.00,
        price_change_pct=-0.0556,
        sma_20=26.80,
        sma_50=22.00,
        volatility=0.034,
    )

@pytest.fixture
def mock_portfolio():
    return PortfolioState(
        account_equity=100_000.0,
        buying_power=400_000.0,
        cash=100_000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0,
        position_quantities={},
    )

def _make_proposal(action: Action, confidence: float = 0.63) -> StrategyProposal:
    return StrategyProposal(
        action=action,
        confidence=confidence,
        thesis="Test thesis.",
        evidence="Price data.",
        assumptions=["Markets remain orderly."],
        invalidation_conditions=["Price drops below SMA50."],
        suggested_stop_loss=None,
        reasoning_summary="Summary.",
        memory_influence=MemoryInfluence.NONE,
        memory_reasoning="",
    )

def test_successful_no_trade(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.NO_TRADE)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "PLTR"
    assert body["status"] == "ADVISORY_ONLY"
    assert body["analysis"]["action"] == "NO_TRADE"
    assert body["analysis"]["confidence"] == pytest.approx(0.63)

def test_successful_buy(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.BUY, confidence=0.82)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    assert res.json()["analysis"]["action"] == "BUY"
    assert res.json()["status"] == "ADVISORY_ONLY"

def test_successful_sell(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.SELL, confidence=0.71)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    assert res.json()["analysis"]["action"] == "SELL"

def test_symbol_normalized_to_uppercase(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.NO_TRADE)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot) as mock_fetch, \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/pltr")
    assert res.status_code == 200
    mock_fetch.assert_called_once_with("PLTR")
    assert res.json()["symbol"] == "PLTR"

def test_market_snapshot_fetched_server_side(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.NO_TRADE)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot) as mock_fetch, \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR", json=None)
    assert res.status_code == 200
    mock_fetch.assert_called_once_with("PLTR")
    assert res.json()["market_snapshot"]["current_price"] == pytest.approx(25.50)

def test_response_contains_snapshot_used_for_analysis(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.NO_TRADE)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    snap = res.json()["market_snapshot"]
    assert snap["symbol"] == "PLTR"
    assert snap["sma_20"] == pytest.approx(26.80)
    assert snap["sma_50"] == pytest.approx(22.00)

def test_confidence_within_bounds(mock_snapshot, mock_portfolio):
    for conf in [0.0, 0.5, 1.0]:
        proposal = _make_proposal(Action.NO_TRADE, confidence=conf)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
             patch("tradepilot.api.MemoryStore"):
            MockAgent.return_value.analyze_market.return_value = proposal
            res = client.post("/api/analyze/PLTR")
        assert res.status_code == 200
        assert 0.0 <= res.json()["analysis"]["confidence"] <= 1.0

def test_market_data_value_error_returns_400(mock_portfolio):
    with patch("tradepilot.api.fetch_market_snapshot",
               side_effect=ValueError("Insufficient historical data for PLTR.")), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 400
    assert "Insufficient" in res.json()["detail"]

def test_market_data_connection_error_returns_503(mock_portfolio):
    with patch("tradepilot.api.fetch_market_snapshot",
               side_effect=ConnectionError("Failed to fetch from Alpaca")), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio):
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 503

def test_portfolio_failure_returns_503(mock_snapshot):
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state",
               side_effect=ConnectionError("Alpaca trading API unavailable")):
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 503

def test_gemini_failure_returns_503(mock_snapshot, mock_portfolio):
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"):
        MockAgent.return_value.analyze_market.return_value = None
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"].lower()

def test_endpoint_does_not_call_risk_engine(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.BUY)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"), \
         patch("tradepilot.core.risk_engine.RiskEngine.evaluate") as mock_risk:
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    mock_risk.assert_not_called()

def test_endpoint_does_not_call_alpaca_execution(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.BUY)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"), \
         patch("tradepilot.alpaca.client.get_alpaca_client") as mock_alpaca:
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    mock_alpaca.assert_not_called()

def test_endpoint_does_not_write_journal(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.BUY)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore"), \
         patch("tradepilot.memory.journal.TradeJournal.save_record") as mock_save:
        MockAgent.return_value.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    mock_save.assert_not_called()

def test_response_always_advisory_only(mock_snapshot, mock_portfolio):
    for action in [Action.BUY, Action.SELL, Action.NO_TRADE]:
        proposal = _make_proposal(action)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
             patch("tradepilot.api.MemoryStore"):
            MockAgent.return_value.analyze_market.return_value = proposal
            res = client.post("/api/analyze/PLTR")
        assert res.json()["status"] == "ADVISORY_ONLY"

def test_analyze_market_receives_server_side_data(mock_snapshot, mock_portfolio):
    proposal = _make_proposal(Action.NO_TRADE)
    with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
         patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
         patch("tradepilot.api.LLMStrategyAgent") as MockAgent, \
         patch("tradepilot.api.MemoryStore") as MockMemory:
        MockMemory.return_value.retrieve_memories.return_value = []
        mock_agent_instance = MockAgent.return_value
        mock_agent_instance.analyze_market.return_value = proposal
        res = client.post("/api/analyze/PLTR")
    assert res.status_code == 200
    call_args = mock_agent_instance.analyze_market.call_args
    assert call_args[0][0] == mock_snapshot
    assert call_args[0][1] == mock_portfolio

def test_insufficient_data_symbol_returns_400():
    with patch("tradepilot.api.fetch_market_snapshot",
               side_effect=ValueError("Insufficient historical data for FAKE.")):
        res = client.post("/api/analyze/FAKE")
    assert res.status_code == 400
