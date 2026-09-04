import argparse
import sys
from tradepilot.alpaca.market_data import fetch_market_snapshot
from tradepilot.alpaca.portfolio import fetch_portfolio_state
from tradepilot.agents.llm_strategy import LLMStrategyAgent
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.config import settings
from tradepilot.core.models import Action

def main():
    parser = argparse.ArgumentParser(description="TradePilot AI Strategy Demo")
    parser.add_argument("symbol", type=str, help="Stock ticker symbol (e.g., NVDA)")
    args = parser.parse_args()
    
    symbol = args.symbol.upper()
    
    print("--- TRADEPILOT AI STRATEGY ---\n")
    print(f"Fetching market data for {symbol}...")
    
    try:
        # 1. Fetch real Alpaca market data
        snapshot = fetch_market_snapshot(symbol)
        
        print("\n--- MARKET DATA ---")
        print(f"{snapshot.symbol}")
        print(f"Price:    ${snapshot.current_price:,.2f}")
        print(f"SMA20:    ${snapshot.sma_20:,.2f}")
        print(f"SMA50:    ${snapshot.sma_50:,.2f}")
        print(f"Momentum: {snapshot.price_change_pct * 100:.2f}%")
        
        # 2. Fetch real paper PortfolioState
        print("\nFetching portfolio state...")
        portfolio = fetch_portfolio_state()
        print(f"Account Equity: ${portfolio.account_equity:,.2f}")
        
        # 3. Call Gemini
        print("\nConsulting AI Analyst...")
        strategy = LLMStrategyAgent()
        proposal = strategy.generate_proposal(snapshot, portfolio)
        
        if not proposal:
            print("\nAI DECISION:\nNO_TRADE")
            sys.exit(0)
            
        print("\nAI DECISION:")
        print(f"{proposal.action.value}")
        
        print(f"\nConfidence:\n{proposal.confidence * 100:.0f}%")
        print(f"\nTHESIS:\n{proposal.reasoning.split('Evidence:')[0].strip()}")
        print(f"\nEVIDENCE:\n{proposal.reasoning.split('Evidence:')[1].split('Summary:')[0].strip()}")
        
        print("\nASSUMPTIONS:")
        for a in proposal.assumptions:
             print(f"- {a}")
             
        print("\nINVALIDATION:")
        for i in proposal.invalidation_conditions:
             print(f"- {i}")
             
        print(f"\nSuggested Stop:\n${proposal.stop_loss:,.2f}")
        
        # 5. Position Sizing
        estimated_cost = proposal.quantity * proposal.current_price
        print(f"\nApplication Position Sizing:\n${estimated_cost:,.2f}\n{proposal.quantity} shares")
        
        # 6. Run RiskEngine
        risk_engine = RiskEngine(
            max_position_pct=settings.max_position_pct,
            max_risk_usd=settings.max_risk_usd,
            max_open_positions=settings.max_open_positions,
            daily_loss_limit=settings.daily_loss_limit
        )
        decision = risk_engine.evaluate(proposal, portfolio)
        
        # 7. Display Result
        print(f"\nRisk Decision:\n{decision.status.value}")
        if decision.status.value == "REJECTED":
            for reason in decision.reasons:
                print(f" - {reason}")
                
        print("\nNO ORDER SUBMITTED")
            
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
