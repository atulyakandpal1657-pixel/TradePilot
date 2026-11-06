"""
main.py

LEGACY CLI DEMONSTRATION RUNNER:
Initial Step 1 prototype demonstration script for the early Orchestrator loop.

In production TradePilot v1.0, run the FastAPI application:
    uv run uvicorn tradepilot.api:app --reload
Or run CLI commands via:
    uv run python -m tradepilot.cli --help
"""

from tradepilot.core.models import TradeProposal, Action, PortfolioState, OrderType
from tradepilot.core.orchestrator import Orchestrator
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.config import settings
import uuid

def main():
    risk_engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit
    )
    
    orchestrator = Orchestrator(risk_engine=risk_engine)
    
    # Mock Portfolio State
    portfolio_state = PortfolioState(
        account_equity=100000.0,
        buying_power=100000.0,
        cash=100000.0,
        current_daily_loss=0.0,
        open_positions=["MSFT"],
        number_of_open_positions=1
    )
    
    # Mock Strategy Proposal 1 (Should Pass)
    proposal_1 = TradeProposal(
        symbol="AAPL",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        quantity=5,
        current_price=150.0,
        confidence=0.85,
        reasoning="Strong technical breakout",
        assumptions=["Market remains bullish"],
        invalidation_conditions=["Price drops below 145"],
        stop_loss=145.0
    )
    
    print("Testing Valid Proposal...")
    result_1 = orchestrator.run_cycle(proposal_1, portfolio_state)
    print(f"Final Result: {result_1}\n")
    
    # Mock Strategy Proposal 2 (Should Fail Risk)
    proposal_2 = TradeProposal(
        symbol="TSLA",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        quantity=1000, # Too large, insufficient buying power & max position pct
        current_price=150.0,
        confidence=0.9,
        reasoning="Earnings anticipation",
        assumptions=["Earnings beat"],
        invalidation_conditions=["CEO tweet"],
        stop_loss=100.0
    )
    
    print("Testing Risky Proposal...")
    result_2 = orchestrator.run_cycle(proposal_2, portfolio_state)
    print(f"Final Result: {result_2}\n")

if __name__ == "__main__":
    main()
