import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pydantic import ValidationError

from tradepilot.core.models import MarketSnapshot, PortfolioState, Action, StrategyProposal, TradeProposal, RiskStatus
from tradepilot.agents.llm_strategy import LLMStrategyAgent
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.config import settings

@pytest.fixture
def snapshot():
    return MarketSnapshot(
        symbol="AAPL",
        current_price=150.0,
        timestamp=datetime.now(timezone.utc),
        volume=1000000,
        previous_close=148.0,
        price_change_pct=0.013,
        sma_20=145.0,
        sma_50=140.0,
        volatility=0.015
    )

@pytest.fixture
def portfolio():
    return PortfolioState(
        account_equity=100000.0,
        buying_power=400000.0,
        cash=100000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0
    )

@pytest.fixture
def risk_engine():
    return RiskEngine(
        max_position_pct=0.1,
        max_risk_usd=1000.0,
        max_open_positions=5,
        daily_loss_limit=2000.0
    )

def create_mock_response(strategy_proposal_dict=None, raise_error=False, raw_text=""):
    class MockResponse:
        def __init__(self):
            if raise_error:
                raise Exception("API Error")
            if strategy_proposal_dict is not None:
                try:
                    self.parsed = StrategyProposal(**strategy_proposal_dict)
                except ValidationError:
                    # Let the LLMStrategyAgent handle the error just like the real SDK would
                    # if we fallback to json string parsing, but here the SDK throws ValidationError directly 
                    # if the schema doesn't match when using 'parsed'.
                    # For testing we'll just mock it so that the code triggers the catch block
                    raise ValidationError.from_exception_data("Mock Error", [])
            else:
                self.parsed = None
            self.text = raw_text
    return MockResponse()

@patch('google.genai.Client')
def test_valid_structured_response(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Good",
        evidence="High volume",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=140.0,
        reasoning_summary="Summary"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    # Must explicitly enable gemini key for test
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    
    proposal = agent.generate_proposal(snapshot, portfolio)
    
    assert proposal is not None
    assert proposal.action == Action.BUY
    assert proposal.quantity > 0
    assert proposal.stop_loss == 140.0

@patch('google.genai.Client')
def test_invalid_confidence(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Validation error raised by Pydantic during mock construction
    def side_effect(*args, **kwargs):
        raise ValidationError.from_exception_data("Invalid confidence", [])
        
    mock_client.models.generate_content.side_effect = side_effect
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None # NO_TRADE fallback

@patch('google.genai.Client')
def test_invalid_action(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    def side_effect(*args, **kwargs):
        raise ValidationError.from_exception_data("Invalid action", [])
        
    mock_client.models.generate_content.side_effect = side_effect
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None

@patch('google.genai.Client')
def test_missing_required_fields(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    def side_effect(*args, **kwargs):
        raise ValidationError.from_exception_data("Missing field", [])
        
    mock_client.models.generate_content.side_effect = side_effect
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None
    
@patch('google.genai.Client')
def test_invalid_stop_loss(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Good",
        evidence="High volume",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=160.0, # INVALID: above current price (150)
        reasoning_summary="Summary"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    # TradeProposal validation should catch the invalid stop loss and return None
    assert proposal is None

@patch('google.genai.Client')
def test_llm_api_failure(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    mock_client.models.generate_content.side_effect = Exception("503 Service Unavailable")
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None

@patch('google.genai.Client')
def test_empty_response(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = "" # Will fail JSON validation
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None

@patch('google.genai.Client')
def test_llm_proposes_buy_risk_rejects(mock_client_class, snapshot, portfolio, risk_engine):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Good",
        evidence="High volume",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=140.0,
        reasoning_summary="Summary"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    
    # Modify risk engine to reject
    risk_engine.max_risk_usd = 1.0 # Guarantee rejection
    decision = risk_engine.evaluate(proposal, portfolio)
    assert decision.status == RiskStatus.REJECTED

@patch('google.genai.Client')
def test_llm_proposes_buy_risk_approves(mock_client_class, snapshot, portfolio, risk_engine):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Good",
        evidence="High volume",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=140.0,
        reasoning_summary="Summary"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test_key"
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    
    decision = risk_engine.evaluate(proposal, portfolio)
    assert decision.status == RiskStatus.APPROVED

@patch('google.genai.Client')
def test_llm_cannot_control_quantity(mock_client_class, snapshot, portfolio):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    # StrategyProposal explicitly lacks quantity field
    mock_response.parsed = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Good",
        evidence="High volume",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=140.0,
        reasoning_summary="Summary"
    )
    mock_client.models.generate_content.return_value = mock_response
    
    settings.gemini_api_key = "test_key"
    settings.max_position_pct = 0.1 # 10% of 100k = 10k
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    
    expected_qty = int((100000.0 * 0.1) / 150.0) # 66
    assert proposal.quantity == expected_qty
    
def test_missing_api_key_produces_no_trade(snapshot, portfolio):
    settings.gemini_api_key = ""
    agent = LLMStrategyAgent()
    proposal = agent.generate_proposal(snapshot, portfolio)
    assert proposal is None
