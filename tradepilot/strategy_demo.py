import argparse
import sys
from tradepilot.alpaca.market_data import fetch_market_snapshot
from tradepilot.alpaca.portfolio import fetch_portfolio_state
from tradepilot.agents.strategy import DeterministicBaselineStrategy
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.config import settings

def main():
    parser = argparse.ArgumentParser(description="TradePilot Strategy Demo")
    parser.add_argument("symbol", type=str, help="Stock ticker symbol (e.g., NVDA)")
    args = parser.parse_args()
    
    symbol = args.symbol.upper()
    
    print("--- TRADEPILOT STRATEGY DEMO ---\n")
    print(f"Fetching market data for {symbol}...")
    
    try:
        # 1. Fetch real Alpaca market data
        snapshot = fetch_market_snapshot(symbol)
        
        print("\n--- MARKET DATA ---")
        print(f"Symbol:   {snapshot.symbol}")
        print(f"Price:    ${snapshot.current_price:,.2f}")
        print(f"SMA20:    ${snapshot.sma_20:,.2f}")
        print(f"SMA50:    ${snapshot.sma_50:,.2f}")
        print(f"Momentum: {snapshot.price_change_pct * 100:.2f}%")
        
        # 2. Fetch real paper PortfolioState
        print("\nFetching portfolio state...")
        portfolio = fetch_portfolio_state()
        print(f"Account Equity: ${portfolio.account_equity:,.2f}")
        
        # 3. Generate baseline strategy proposal
        strategy = DeterministicBaselineStrategy()
        proposal = strategy.generate_proposal(snapshot, portfolio)
        
        print("\n--- STRATEGY DECISION ---")
        if not proposal:
            print("NO TRADE - Baseline strategy criteria not met.")
            sys.exit(0)
            
        print(f"Action:     {proposal.action.value}")
        print(f"Quantity:   {proposal.quantity}")
        print(f"Confidence: {proposal.confidence * 100:.0f}%")
        print(f"Stop Loss:  ${proposal.stop_loss:,.2f}")
        
        print(f"\nThesis:\n{proposal.reasoning}")
        print(f"\nInvalidation:\n{', '.join(proposal.invalidation_conditions)}")
        
        # 4. Run through Risk Engine
        print("\n--- RISK CHECK ---")
        risk_engine = RiskEngine(
            max_position_pct=settings.max_position_pct,
            max_risk_usd=settings.max_risk_usd,
            max_open_positions=settings.max_open_positions,
            daily_loss_limit=settings.daily_loss_limit
        )
        
        estimated_cost = proposal.quantity * proposal.current_price
        max_allowed = portfolio.account_equity * settings.max_position_pct
        
        print(f"Proposed Position Size: ${estimated_cost:,.2f}")
        print(f"Maximum allowed limit:  ${max_allowed:,.2f}")
        
        decision = risk_engine.evaluate(proposal, portfolio)
        
        print(f"\nRisk Decision: {decision.status.value}")
        for reason in decision.reasons:
            print(f" - {reason}")
            
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
