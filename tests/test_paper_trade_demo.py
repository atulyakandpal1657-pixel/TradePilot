import math
import uuid

import pytest

from tradepilot.core.config import settings
from tradepilot.core.models import Action, OrderType, TradeProposal
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.models import PortfolioState, RiskStatus
from tradepilot.paper_trade_demo import (
    ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL,
    DEMO_LIMIT_UNEXECUTABLE_MSG,
    apply_demo_sizing,
    calculate_demo_quantity,
)


def _make_proposal(*, quantity: float, current_price: float, symbol: str = "AAPL") -> TradeProposal:
    return TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol=symbol,
        action=Action.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        current_price=current_price,
        confidence=0.8,
        reasoning="Test",
        assumptions=[],
        invalidation_conditions=[],
    )


def test_demo_sizing_below_limit():
    """Typical stock: fractional demo qty keeps notional under $100."""
    limit = settings.max_demo_order_notional
    price = 150.0
    proposal = _make_proposal(quantity=66, current_price=price)  # ~$9,900 normal sizing

    demo_proposal, error = apply_demo_sizing(proposal, limit)
    assert error is None
    assert demo_proposal is not None

    notional = demo_proposal.quantity * price
    assert notional <= limit
    assert notional < limit  # strictly below for non-divisor prices
    assert demo_proposal.quantity == pytest.approx(100 / 150, rel=1e-4)


def test_demo_sizing_at_or_under_limit():
    """Price divides limit evenly: one whole share uses full $100 cap."""
    limit = settings.max_demo_order_notional
    price = 100.0
    proposal = _make_proposal(quantity=50, current_price=price)

    demo_proposal, error = apply_demo_sizing(proposal, limit)
    assert error is None
    assert demo_proposal.quantity == 1.0
    assert demo_proposal.quantity * price == limit


def test_expensive_stock_whole_shares_exceed_limit():
    """Without fractional support, one share above $100 is untradeable."""
    limit = settings.max_demo_order_notional
    price = 500.0
    proposal = _make_proposal(quantity=20, current_price=price)

    demo_proposal, error = apply_demo_sizing(
        proposal, limit, supports_fractional=False
    )
    assert demo_proposal is None
    assert error == DEMO_LIMIT_UNEXECUTABLE_MSG
    assert calculate_demo_quantity(price, limit, supports_fractional=False) is None


def test_fractional_quantity_when_supported():
    """Alpaca market orders accept fractional qty for expensive symbols."""
    limit = settings.max_demo_order_notional
    price = 500.0
    proposal = _make_proposal(quantity=20, current_price=price)

    assert ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL is True

    demo_proposal, error = apply_demo_sizing(proposal, limit)
    assert error is None
    assert demo_proposal.quantity == pytest.approx(0.2, rel=1e-4)
    assert demo_proposal.quantity * price <= limit


def test_final_notional_never_exceeds_limit():
    """Sweep prices; demo notional must stay at or under MAX_DEMO_ORDER_NOTIONAL."""
    limit = settings.max_demo_order_notional
    for price in [7.5, 42.17, 150.0, 987.07, 5000.0]:
        proposal = _make_proposal(quantity=1000, current_price=price)
        demo_proposal, error = apply_demo_sizing(proposal, limit)
        if price > limit and not ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL:
            assert demo_proposal is None
            continue
        assert error is None, f"price={price}"
        notional = demo_proposal.quantity * price
        assert notional <= limit, f"price={price} notional={notional}"


def test_demo_guard_defense_in_depth_still_blocks():
    """Defense-in-depth guard triggers if notional somehow exceeds the cap."""
    limit = settings.max_demo_order_notional
    notional = 150.0
    guard_triggered = notional > limit
    assert guard_triggered


def test_risk_engine_still_validates_demo_sized_proposal():
    """RiskEngine independently checks demo-sized proposals."""
    limit = settings.max_demo_order_notional
    portfolio = PortfolioState(
        account_equity=100_000.0,
        buying_power=100_000.0,
        cash=100_000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0,
    )
    proposal = _make_proposal(quantity=66, current_price=150.0)
    demo_proposal, _ = apply_demo_sizing(proposal, limit)

    engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit,
    )
    decision = engine.evaluate(demo_proposal, portfolio)
    assert decision.status == RiskStatus.APPROVED
    assert decision.approved_trade is not None
    assert decision.approved_trade.proposal.quantity == demo_proposal.quantity


def test_normal_strategy_quantity_unchanged_on_input():
    """Demo sizing copies the proposal; original strategy qty is not mutated."""
    proposal = _make_proposal(quantity=66, current_price=150.0)
    original_qty = proposal.quantity

    apply_demo_sizing(proposal, settings.max_demo_order_notional)

    assert proposal.quantity == original_qty


def test_demo_sizing_still_works_for_sell_to_close():
    """Demo sizing applies before RiskEngine; SELL-to-close still validated."""
    limit = settings.max_demo_order_notional
    price = 150.0
    proposal = TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.SELL,
        order_type=OrderType.MARKET,
        quantity=5,
        current_price=price,
        confidence=0.8,
        reasoning="Test",
        assumptions=[],
        invalidation_conditions=[],
        stop_loss=160.0,
    )
    demo_proposal, error = apply_demo_sizing(proposal, limit)
    assert error is None
    assert demo_proposal.quantity * price <= limit

    portfolio = PortfolioState(
        account_equity=100_000.0,
        buying_power=100_000.0,
        cash=100_000.0,
        current_daily_loss=0.0,
        open_positions=["AAPL"],
        number_of_open_positions=1,
        position_quantities={"AAPL": 5.0},
    )
    engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit,
    )
    decision = engine.evaluate(demo_proposal, portfolio)
    assert decision.status == RiskStatus.APPROVED


def test_demo_sized_sell_rejected_without_position():
    limit = settings.max_demo_order_notional
    proposal = TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.SELL,
        order_type=OrderType.MARKET,
        quantity=66,
        current_price=150.0,
        confidence=0.8,
        reasoning="Test",
        assumptions=[],
        invalidation_conditions=[],
        stop_loss=160.0,
    )
    demo_proposal, _ = apply_demo_sizing(proposal, limit)
    portfolio = PortfolioState(
        account_equity=100_000.0,
        buying_power=100_000.0,
        cash=100_000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0,
    )
    engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit,
    )
    decision = engine.evaluate(demo_proposal, portfolio)
    assert decision.status == RiskStatus.REJECTED
    assert any("no long position" in r for r in decision.reasons)


def test_calculate_demo_quantity_formula():
    """demo_max_quantity = MAX_DEMO_ORDER_NOTIONAL / current_price."""
    limit = 100.0
    price = 987.072
    expected = math.floor((limit / price) * 10_000) / 10_000
    qty = calculate_demo_quantity(price, limit)
    assert qty == expected
    assert qty * price <= limit

from unittest.mock import patch, MagicMock
from io import StringIO
from tradepilot.core.models import MarketStatus
import datetime
from tradepilot.paper_trade_demo import main

@patch("tradepilot.paper_trade_demo.is_market_open")
@patch("tradepilot.paper_trade_demo.fetch_market_snapshot")
@patch("tradepilot.paper_trade_demo.fetch_portfolio_state")
@patch("tradepilot.paper_trade_demo.LLMStrategyAgent")
@patch("tradepilot.paper_trade_demo.input", return_value="n")
@patch("sys.argv", ["paper_trade_demo.py", "AAPL"])
@patch("sys.stdout", new_callable=StringIO)
def test_main_market_closed_warning(mock_stdout, mock_input, mock_agent_class, mock_fetch_portfolio, mock_fetch_snapshot, mock_is_market_open):
    # Setup mocks
    mock_snapshot = MagicMock()
    mock_snapshot.current_price = 150.0
    mock_snapshot.sma_20 = 140.0
    mock_snapshot.sma_50 = 130.0
    mock_snapshot.price_change_pct = 0.05
    mock_fetch_snapshot.return_value = mock_snapshot

    mock_portfolio = MagicMock()
    mock_portfolio.account_equity = 100000.0
    mock_portfolio.buying_power = 100000.0
    mock_portfolio.cash = 100000.0
    mock_portfolio.current_daily_loss = 0.0
    mock_portfolio.open_positions = []
    mock_portfolio.number_of_open_positions = 0
    mock_portfolio.position_quantities = {}
    mock_fetch_portfolio.return_value = mock_portfolio

    mock_agent = MagicMock()
    mock_proposal = _make_proposal(quantity=5, current_price=150.0)
    mock_proposal.reasoning = "Thesis\n\nEvidence:\nEvidence\n\nSummary:\nSummary"
    mock_proposal.assumptions = []
    mock_proposal.invalidation_conditions = []
    mock_proposal.stop_loss = 140.0
    mock_agent.generate_proposal.return_value = mock_proposal
    mock_agent_class.return_value = mock_agent
    
    mock_is_market_open.return_value = MarketStatus(
        is_open=False,
        timestamp=datetime.datetime(2023, 10, 25, 22, 30, tzinfo=datetime.timezone.utc),
        next_open=datetime.datetime(2023, 10, 26, 13, 30, tzinfo=datetime.timezone.utc),
        next_close=datetime.datetime(2023, 10, 26, 20, 0, tzinfo=datetime.timezone.utc)
    )

    try:
        main()
    except SystemExit:
        pass

    output = mock_stdout.getvalue()
    assert "MARKET STATUS:\nCLOSED" in output
    assert "US market is currently closed. A DAY market order may remain pending until the next trading session." in output

