import argparse
import math
import sys
import uuid
import datetime

from tradepilot.alpaca.market_data import fetch_market_snapshot
from tradepilot.alpaca.portfolio import fetch_portfolio_state
from tradepilot.alpaca.execution import AlpacaPaperExecutor
from tradepilot.alpaca.clock import is_market_open
from tradepilot.agents.llm_strategy import LLMStrategyAgent
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.config import settings
from tradepilot.core.models import Action, ApprovedTrade, RiskDecision, RiskStatus
from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore
from tradepilot.core.models import TradeRecord, TradeProposal

# Alpaca MarketOrderRequest accepts fractional qty for stock market orders.
ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL = True

DEMO_LIMIT_UNEXECUTABLE_MSG = "Cannot execute within $100 demo limit for this symbol."


def calculate_demo_quantity(
    current_price: float,
    max_demo_notional: float,
    *,
    supports_fractional: bool = ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL,
) -> float | None:
    """Return demo execution quantity capped to max_demo_notional, or None if untradeable."""
    if current_price <= 0:
        return None

    demo_max_quantity = max_demo_notional / current_price

    if supports_fractional:
        # Round down so qty * price never exceeds the demo cap (float-safe).
        qty = math.floor(demo_max_quantity * 10_000) / 10_000
    else:
        qty = float(math.floor(demo_max_quantity))

    if qty <= 0:
        return None

    return qty


def apply_demo_sizing(
    proposal: TradeProposal,
    max_demo_notional: float,
    *,
    supports_fractional: bool = ALPACA_MARKET_ORDER_SUPPORTS_FRACTIONAL,
) -> tuple[TradeProposal | None, str | None]:
    """
    Return a copy of proposal with demo-capped quantity, or (None, error_message).
    Normal strategy sizing on the input proposal is preserved for display only.
    """
    demo_quantity = calculate_demo_quantity(
        proposal.current_price,
        max_demo_notional,
        supports_fractional=supports_fractional,
    )
    if demo_quantity is None:
        return None, DEMO_LIMIT_UNEXECUTABLE_MSG

    demo_proposal = proposal.model_copy(update={"quantity": demo_quantity})
    demo_notional = demo_proposal.quantity * demo_proposal.current_price
    if demo_notional > max_demo_notional:
        return None, DEMO_LIMIT_UNEXECUTABLE_MSG

    return demo_proposal, None


def main():
    parser = argparse.ArgumentParser(description="TradePilot Paper Trade Demo")
    parser.add_argument("symbol", type=str, help="Stock ticker symbol (e.g., NVDA)")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    print("--- TRADEPILOT PAPER TRADE DEMO ---\n")
    print(f"Fetching market data for {symbol}...")

    try:
        snapshot = fetch_market_snapshot(symbol)
        portfolio = fetch_portfolio_state()

        store = MemoryStore()
        memories = store.retrieve_memories(symbol)
        if memories:
            print(f"Found {len(memories)} relevant memory/memories for {symbol}.")

        print("\nConsulting AI Analyst...")
        strategy = LLMStrategyAgent()
        proposal = strategy.generate_proposal(snapshot, portfolio, memories=memories)

        if not proposal:
            print("\nAI DECISION:\nNO_TRADE")
            sys.exit(0)

        # Market data header
        print(f"\n{symbol}")
        print(f"Price:    ${snapshot.current_price:,.2f}")
        print(f"SMA20:    ${snapshot.sma_20:,.2f}")
        print(f"SMA50:    ${snapshot.sma_50:,.2f}")
        print(f"Momentum: {snapshot.price_change_pct * 100:.2f}%")

        # Parse reasoning parts
        reasoning_parts = proposal.reasoning.split("\n\nEvidence:\n")
        thesis = reasoning_parts[0].strip()
        rest = reasoning_parts[1] if len(reasoning_parts) > 1 else ""
        evidence_parts = rest.split("\n\nSummary:\n")
        evidence = evidence_parts[0].strip()

        print(f"\nAI DECISION:\n{proposal.action.value}")
        print(f"\nConfidence:\n{proposal.confidence * 100:.0f}%")
        print(f"\nTHESIS:\n{thesis}")
        print(f"\nEVIDENCE:\n{evidence}")

        print("\nASSUMPTIONS:")
        for a in proposal.assumptions:
            print(f"  - {a}")

        print("\nINVALIDATION:")
        for c in proposal.invalidation_conditions:
            print(f"  - {c}")

        if proposal.stop_loss:
            print(f"\nSuggested Stop:\n${proposal.stop_loss:,.2f}")

        # Application-controlled position sizing (unchanged strategy calculation)
        normal_notional = proposal.quantity * proposal.current_price
        print(f"\nNormal strategy sizing:\n${normal_notional:,.2f}")

        demo_proposal, demo_error = apply_demo_sizing(
            proposal, settings.max_demo_order_notional
        )
        if demo_error:
            print(f"\n{demo_error}")
            print("\nNO ORDER SUBMITTED.")
            sys.exit(0)

        demo_notional = demo_proposal.quantity * demo_proposal.current_price
        print(f"\nDemo-adjusted sizing:\n${demo_notional:,.2f}\n{demo_proposal.quantity} shares")

        # Risk Engine validates the demo-sized proposal independently
        risk_engine = RiskEngine(
            max_position_pct=settings.max_position_pct,
            max_risk_usd=settings.max_risk_usd,
            max_open_positions=settings.max_open_positions,
            daily_loss_limit=settings.daily_loss_limit
        )
        decision = risk_engine.evaluate(demo_proposal, portfolio)

        print(f"\nRisk Decision:\n{decision.status.value}")
        if decision.status == RiskStatus.REJECTED:
            for r in decision.reasons:
                print(f"  - {r}")
            sys.exit(0)

        approved_trade = decision.approved_trade

        # Demo Safety Guard (defense-in-depth on top of demo sizing + RiskEngine)
        if demo_notional > settings.max_demo_order_notional:
            print(f"\n[DEMO SAFETY GUARD]")
            print(f"  Order notional ${demo_notional:,.2f} exceeds")
            print(f"  MAX_DEMO_ORDER_NOTIONAL = ${settings.max_demo_order_notional:.2f}")
            print("  Increase MAX_DEMO_ORDER_NOTIONAL in .env to submit larger orders.")
            print("\nNO ORDER SUBMITTED.")
            sys.exit(0)

        print(f"\nApproved Trade:")
        print(f"  Symbol:    {symbol}")
        print(f"  Action:    {demo_proposal.action.value}")
        print(f"  Quantity:  {demo_proposal.quantity} shares")
        print(f"  Notional:  ${demo_notional:,.2f}")

        market_status = is_market_open()
        print("\nMARKET STATUS:")
        if market_status.is_open:
            print("OPEN")
        else:
            print("CLOSED")
            print("US market is currently closed. A DAY market order may remain pending until the next trading session.")

        print("\nEXECUTE THIS PAPER ORDER? [y/N] ", end="", flush=True)
        choice = input().strip().lower()

        if choice == 'y':
            print("\nSubmitting to Alpaca Paper Trading...")
            executor = AlpacaPaperExecutor()
            result = executor.execute(approved_trade)

            print(f"\nExecution Status: {result.status.value}")
            print(f"Client Order ID:  {result.client_order_id}")
            print(f"Alpaca Order ID:  {result.alpaca_order_id}")
            print(f"Message:          {result.message}")

            # Save to journal
            journal = TradeJournal()
            record = TradeRecord(
                trade_id=demo_proposal.trade_id,
                thesis_id=demo_proposal.thesis_id,
                alpaca_order_id=result.alpaca_order_id,
                symbol=demo_proposal.symbol,
                action=demo_proposal.action,
                quantity=demo_proposal.quantity,
                filled_quantity=result.filled_quantity,
                average_fill_price=result.average_fill_price,
                original_thesis=demo_proposal.reasoning,
                original_confidence=demo_proposal.confidence,
                assumptions=demo_proposal.assumptions,
                invalidation_conditions=demo_proposal.invalidation_conditions,
                risk_decision=RiskStatus.APPROVED,
                execution_status=result.status
            )
            journal.save_record(record)
            print("\nTrade saved to journal.")
            print("NO AUTONOMOUS TRADING. Manual confirmation required for every order.")

        else:
            print("\nNO ORDER SUBMITTED.")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
