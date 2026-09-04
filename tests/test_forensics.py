import pytest
from unittest.mock import patch, MagicMock
import uuid
from pydantic import ValidationError
from tradepilot.core.models import TradeRecord, Action, ExecutionStatus, RiskStatus, TradeForensics, Quality
from tradepilot.agents.forensics import ForensicsAgent
from tradepilot.core.config import settings

@pytest.fixture
def closed_trade():
    return TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Strong volume",
        original_confidence=0.8,
        assumptions=["Volume persists"],
        invalidation_conditions=["Volume drops"],
        risk_decision=RiskStatus.APPROVED,
        entry_price=150.0,
        exit_price=160.0,
        realized_pnl=100.0,
        realized_return_pct=0.066,
        execution_status=ExecutionStatus.FILLED
    )

@patch('google.genai.Client')
def test_forensics_valid_response(mock_client_class, closed_trade):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = TradeForensics(
        trade_id=closed_trade.trade_id,
        thesis_correctness=Quality.CORRECT,
        successful_assumptions=["Volume persists"],
        failed_assumptions=[],
        actual_market_conditions="High volume uptrend",
        primary_failure_or_success_factor="Good timing",
        thesis_quality=Quality.CORRECT,
        timing_quality=Quality.CORRECT,
        risk_assessment_quality=Quality.CORRECT,
        lesson="Buy breakouts on volume",
        confidence=0.9,
        evidence_summary="Volume held up"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test"
    agent = ForensicsAgent()
    forensics = agent.analyze_trade(closed_trade)
    
    assert forensics is not None
    assert forensics.thesis_correctness == Quality.CORRECT

@patch('google.genai.Client')
def test_forensics_api_failure(mock_client_class, closed_trade):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    mock_client.models.generate_content.side_effect = Exception("API error")
    
    settings.gemini_api_key = "test"
    agent = ForensicsAgent()
    assert agent.analyze_trade(closed_trade) is None

@patch('google.genai.Client')
def test_forensics_malformed_output(mock_client_class, closed_trade):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    def side_effect(*args, **kwargs):
        raise ValidationError.from_exception_data("Invalid", [])
    mock_client.models.generate_content.side_effect = side_effect
    
    settings.gemini_api_key = "test"
    agent = ForensicsAgent()
    assert agent.analyze_trade(closed_trade) is None
