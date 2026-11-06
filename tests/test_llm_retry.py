"""
Tests for LLMStrategyAgent retry logic and chats.create SDK migration.
"""

import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from google.genai import errors as genai_errors

from tradepilot.api import app
from tradepilot.agents.llm_strategy import LLMStrategyAgent, _MAX_RETRIES, _RETRY_DELAYS
from tradepilot.core.models import (
    MarketSnapshot,
    PortfolioState,
    StrategyProposal,
    Action,
    MemoryInfluence,
)
from tradepilot.core.config import settings

api_client = TestClient(app)
FIXED_TS = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def snapshot():
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
def portfolio():
    return PortfolioState(
        account_equity=100_000.0,
        buying_power=400_000.0,
        cash=100_000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0,
        position_quantities={},
    )

def _good_proposal() -> StrategyProposal:
    return StrategyProposal(
        action=Action.NO_TRADE,
        confidence=0.55,
        thesis="Test thesis.",
        evidence="Price data.",
        assumptions=["Stable market."],
        invalidation_conditions=["SMA50 breaks down."],
        suggested_stop_loss=None,
        reasoning_summary="Summary.",
        memory_influence=MemoryInfluence.NONE,
        memory_reasoning="",
    )

def _make_503() -> genai_errors.ServerError:
    """Build a real genai_errors.ServerError with code=503 (required by side_effect)."""
    return genai_errors.ServerError(503, {"status": "UNAVAILABLE", "message": "overloaded"})

def _make_agent_with_key() -> LLMStrategyAgent:
    settings.gemini_api_key = "test_key"
    return LLMStrategyAgent()

def _make_mock_client(side_effects):
    """
    side_effects: list of values/exceptions for send_message calls.
    Returns (mock_client, mock_chat).
    """
    mock_chat = MagicMock()
    mock_chat.send_message.side_effect = side_effects
    mock_client = MagicMock()
    mock_client.chats.create.return_value = mock_chat
    return mock_client, mock_chat


# ── 1. _is_transient classification ──────────────────────────────────────────

def test_is_transient_true_for_503():
    assert LLMStrategyAgent._is_transient(_make_503()) is True

def test_is_transient_false_for_generic_exception():
    assert LLMStrategyAgent._is_transient(ValueError("bad")) is False

def test_is_transient_false_for_500():
    exc = genai_errors.ServerError(500, {"status": "INTERNAL", "message": "server error"})
    assert LLMStrategyAgent._is_transient(exc) is False


# ── 2. Success on first attempt ───────────────────────────────────────────────

@patch("google.genai.Client")
def test_call_gemini_success_first_attempt(mock_client_class):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, mock_chat = _make_mock_client([good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()
    response = agent._call_gemini("sys", "prompt")

    assert response == good
    mock_client.chats.create.assert_called_once()
    mock_chat.send_message.assert_called_once_with("prompt")
    mock_client.models.generate_content.assert_not_called()


# ── 3. Success after one 503 retry ────────────────────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_call_gemini_success_after_one_retry(mock_client_class, mock_sleep):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, mock_chat = _make_mock_client([_make_503(), good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()
    response = agent._call_gemini("sys", "prompt")

    assert response == good
    assert mock_chat.send_message.call_count == 2
    mock_sleep.assert_called_once_with(_RETRY_DELAYS[0])  # 2s


# ── 4. Success after two 503 retries ─────────────────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_call_gemini_success_after_two_retries(mock_client_class, mock_sleep):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, mock_chat = _make_mock_client([_make_503(), _make_503(), good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()
    response = agent._call_gemini("sys", "prompt")

    assert response == good
    assert mock_chat.send_message.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(_RETRY_DELAYS[0])  # 2s
    mock_sleep.assert_any_call(_RETRY_DELAYS[1])  # 4s


# ── 5. RuntimeError after all 3 retries exhausted ────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_call_gemini_raises_after_all_retries_exhausted(mock_client_class, mock_sleep):
    errors = [_make_503() for _ in range(_MAX_RETRIES + 1)]
    mock_client, mock_chat = _make_mock_client(errors)
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()

    with pytest.raises(RuntimeError, match="unavailable after"):
        agent._call_gemini("sys", "prompt")

    assert mock_chat.send_message.call_count == _MAX_RETRIES + 1
    assert mock_sleep.call_count == _MAX_RETRIES
    mock_sleep.assert_any_call(_RETRY_DELAYS[2])  # 8s on last retry


# ── 6. Non-transient errors not retried ──────────────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_non_transient_error_not_retried(mock_client_class, mock_sleep):
    auth_error = genai_errors.ClientError(401, {"status": "UNAUTHENTICATED", "message": "bad key"})
    mock_client, mock_chat = _make_mock_client([auth_error])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()

    with pytest.raises(Exception):
        agent._call_gemini("sys", "prompt")

    assert mock_chat.send_message.call_count == 1  # only one attempt
    mock_sleep.assert_not_called()


# ── 7. analyze_market propagates RuntimeError ─────────────────────────────────

@patch("google.genai.Client")
def test_analyze_market_propagates_runtime_error(mock_client_class, snapshot, portfolio):
    mock_client_class.return_value = MagicMock()
    agent = _make_agent_with_key()

    with patch.object(agent, "_call_gemini", side_effect=RuntimeError("all retries failed")):
        with pytest.raises(RuntimeError):
            agent.analyze_market(snapshot, portfolio)


# ── 8. HTTP 503 when retries exhausted ───────────────────────────────────────

@patch("tradepilot.api.fetch_market_snapshot")
@patch("tradepilot.api.fetch_portfolio_state")
@patch("tradepilot.api.LLMStrategyAgent")
@patch("tradepilot.api.MemoryStore")
def test_api_returns_503_when_retries_exhausted(
    MockMemory, MockAgent, mock_portfolio_fn, mock_snapshot_fn, snapshot, portfolio
):
    mock_snapshot_fn.return_value = snapshot
    mock_portfolio_fn.return_value = portfolio
    MockMemory.return_value.retrieve_memories.return_value = []
    MockAgent.return_value.analyze_market.side_effect = RuntimeError("Gemini unavailable after 3 retries")

    res = api_client.post("/api/analyze/PLTR")

    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"].lower()


# ── 9. No journal writes during retries ──────────────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_no_journal_writes_during_retries(mock_client_class, mock_sleep):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, _ = _make_mock_client([_make_503(), good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()

    with patch("tradepilot.memory.journal.TradeJournal.save_record") as mock_save:
        agent._call_gemini("sys", "prompt")
        mock_save.assert_not_called()


# ── 10. No Alpaca execution during retries ────────────────────────────────────

@patch("time.sleep")
@patch("google.genai.Client")
def test_no_alpaca_execution_during_retries(mock_client_class, mock_sleep):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, _ = _make_mock_client([_make_503(), good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()

    with patch("tradepilot.alpaca.client.get_alpaca_client") as mock_alpaca:
        agent._call_gemini("sys", "prompt")
        mock_alpaca.assert_not_called()


# ── 11. Retry constants match spec ────────────────────────────────────────────

def test_retry_delays_match_spec():
    assert _RETRY_DELAYS == [2, 4, 8]
    assert _MAX_RETRIES == 3


# ── 12. chats.create used, not models.generate_content ───────────────────────

@patch("google.genai.Client")
def test_uses_chats_create_not_generate_content(mock_client_class):
    good = MagicMock()
    good.parsed = _good_proposal()
    mock_client, _ = _make_mock_client([good])
    mock_client_class.return_value = mock_client

    agent = _make_agent_with_key()
    agent._call_gemini("sys", "prompt")

    mock_client.chats.create.assert_called_once()
    mock_client.models.generate_content.assert_not_called()
