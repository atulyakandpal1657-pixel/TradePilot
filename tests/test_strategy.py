import pytest
from datetime import datetime, timezone
from tradepilot.core.models import MarketSnapshot, PortfolioState, Action
from tradepilot.agents.strategy import DeterministicBaselineStrategy

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

def create_snapshot(price, sma20, sma50, change_pct):
    return MarketSnapshot(
        symbol="AAPL",
        current_price=price,
        timestamp=datetime.now(timezone.utc),
        volume=1000000.0,
        previous_close=price / (1 + change_pct),
        price_change_pct=change_pct,
        sma_20=sma20,
        sma_50=sma50,
        volatility=0.02
    )

def test_baseline_strategy_bullish(portfolio):
    # Bullish: price (150) > sma20 (140) > sma50 (130) AND positive change
    snapshot = create_snapshot(price=150.0, sma20=140.0, sma50=130.0, change_pct=0.01)
    
    strategy = DeterministicBaselineStrategy()
    proposal = strategy.generate_proposal(snapshot, portfolio)
    
    assert proposal is not None
    assert proposal.action == Action.BUY
    assert proposal.symbol == "AAPL"
    # Position sizing: max_pct=0.1. Equity 100k -> 10k notional. 10k / 150 = 66 shares
    assert proposal.quantity == 66
    assert proposal.stop_loss == 130.0 # Conservative stop at sma50

def test_baseline_strategy_bearish(portfolio):
    # Bearish: price (120) < sma20 (130)
    snapshot = create_snapshot(price=120.0, sma20=130.0, sma50=140.0, change_pct=-0.01)
    
    strategy = DeterministicBaselineStrategy()
    proposal = strategy.generate_proposal(snapshot, portfolio)
    
    assert proposal is None # NO TRADE

def test_baseline_strategy_no_momentum(portfolio):
    # Bullish moving averages, but negative momentum today
    snapshot = create_snapshot(price=150.0, sma20=140.0, sma50=130.0, change_pct=-0.01)
    
    strategy = DeterministicBaselineStrategy()
    proposal = strategy.generate_proposal(snapshot, portfolio)
    
    assert proposal is None

