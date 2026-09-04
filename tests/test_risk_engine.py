import pytest
from pydantic import ValidationError
from tradepilot.core.models import TradeProposal, Action, RiskStatus, PortfolioState, OrderType
from tradepilot.core.risk_engine import RiskEngine

@pytest.fixture
def base_portfolio():
    return PortfolioState(
        account_equity=10000.0,
        buying_power=10000.0,
        cash=10000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0
    )

@pytest.fixture
def risk_engine():
    return RiskEngine(
        max_position_pct=0.2, # 20% max per position
        max_risk_usd=500.0,
        max_open_positions=5,
        daily_loss_limit=1000.0
    )

def create_valid_proposal(**kwargs):
    default_args = dict(
        symbol="AAPL",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        current_price=150.0,
        confidence=0.8,
        reasoning="Good",
        assumptions=[],
        invalidation_conditions=[],
        stop_loss=140.0
    )
    default_args.update(kwargs)
    return TradeProposal(**default_args)

def test_risk_engine_approved(risk_engine, base_portfolio):
    proposal = create_valid_proposal()
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.APPROVED
    assert decision.approved_trade is not None
    assert decision.approved_trade.proposal.symbol == "AAPL"

def test_risk_engine_rejected_insufficient_funds(risk_engine, base_portfolio):
    base_portfolio.buying_power = 1000.0
    proposal = create_valid_proposal(quantity=10, current_price=150.0) # Cost 1500
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Insufficient buying power" in r for r in decision.reasons)

def test_risk_engine_rejected_max_positions(risk_engine, base_portfolio):
    risk_engine.max_open_positions = 2
    base_portfolio.number_of_open_positions = 2
    proposal = create_valid_proposal()
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Max open positions" in r for r in decision.reasons)

def test_risk_engine_rejected_max_risk(risk_engine, base_portfolio):
    risk_engine.max_risk_usd = 50.0
    # 10 shares * (150 - 140) = 100 risk
    proposal = create_valid_proposal(quantity=10, current_price=150.0, stop_loss=140.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Trade risk" in r for r in decision.reasons)

def test_max_position_pct_rejected(risk_engine, base_portfolio):
    # max_position_pct is 0.2 (20%). equity = 10000. max position = 2000.
    proposal = create_valid_proposal(quantity=20, current_price=150.0) # Cost 3000 -> 30% exposure
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Position exposure" in r for r in decision.reasons)

def test_daily_loss_limit_rejected(risk_engine, base_portfolio):
    base_portfolio.current_daily_loss = 1500.0 # > 1000 limit
    proposal = create_valid_proposal()
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Daily loss limit reached" in r for r in decision.reasons)

def test_validation_zero_quantity():
    with pytest.raises(ValidationError):
        create_valid_proposal(quantity=0)

def test_validation_negative_quantity():
    with pytest.raises(ValidationError):
        create_valid_proposal(quantity=-10)
        
def test_validation_zero_price():
    with pytest.raises(ValidationError):
        create_valid_proposal(current_price=0)

def test_validation_invalid_confidence():
    with pytest.raises(ValidationError):
        create_valid_proposal(confidence=1.5)

def test_buy_stop_loss_above_current_price():
    with pytest.raises(ValidationError):
        create_valid_proposal(action=Action.BUY, current_price=150.0, stop_loss=160.0)

def test_sell_stop_loss_below_current_price():
    with pytest.raises(ValidationError):
        create_valid_proposal(action=Action.SELL, current_price=150.0, stop_loss=140.0)

def test_sell_short_risk_rejected(risk_engine, base_portfolio):
    risk_engine.max_risk_usd = 50.0
    base_portfolio.open_positions = ["AAPL"]
    base_portfolio.number_of_open_positions = 1
    base_portfolio.position_quantities = {"AAPL": 10.0}
    # Sell 10 shares at 150. Stop loss at 160. Risk = (160 - 150) * 10 = 100
    proposal = create_valid_proposal(action=Action.SELL, current_price=150.0, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("Trade risk" in r for r in decision.reasons)


def test_sell_no_position_rejected(risk_engine, base_portfolio):
    proposal = create_valid_proposal(action=Action.SELL, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert decision.approved_trade is None
    assert any("no long position" in r for r in decision.reasons)
    assert any("Short selling is not supported" in r for r in decision.reasons)


def test_sell_exceeds_held_quantity_rejected(risk_engine, base_portfolio):
    base_portfolio.open_positions = ["AAPL"]
    base_portfolio.number_of_open_positions = 1
    base_portfolio.position_quantities = {"AAPL": 5.0}
    proposal = create_valid_proposal(action=Action.SELL, quantity=6, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("exceeds held long position" in r for r in decision.reasons)


def test_sell_equal_to_held_quantity_approved(risk_engine, base_portfolio):
    base_portfolio.open_positions = ["AAPL"]
    base_portfolio.number_of_open_positions = 1
    base_portfolio.position_quantities = {"AAPL": 5.0}
    proposal = create_valid_proposal(action=Action.SELL, quantity=5, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.APPROVED
    assert decision.approved_trade is not None


def test_sell_less_than_held_quantity_approved(risk_engine, base_portfolio):
    base_portfolio.open_positions = ["AAPL"]
    base_portfolio.number_of_open_positions = 1
    base_portfolio.position_quantities = {"AAPL": 5.0}
    proposal = create_valid_proposal(action=Action.SELL, quantity=3, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.APPROVED
    assert decision.approved_trade.proposal.quantity == 3


def test_buy_unchanged_with_sell_rules_present(risk_engine, base_portfolio):
    proposal = create_valid_proposal(action=Action.BUY)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.APPROVED


def test_llm_sized_sell_cannot_bypass_no_position_rule(risk_engine, base_portfolio):
    """Large LLM-sized SELL quantity is rejected when no long position exists."""
    proposal = create_valid_proposal(action=Action.SELL, quantity=66, stop_loss=160.0)
    decision = risk_engine.evaluate(proposal, base_portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert decision.approved_trade is None
    assert any("no long position" in r for r in decision.reasons)
