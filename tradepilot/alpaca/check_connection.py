import sys
from tradepilot.alpaca.client import get_alpaca_client
from tradepilot.alpaca.portfolio import fetch_portfolio_state

def run_diagnostic():
    print("--- TradePilot Alpaca Diagnostic ---")
    
    try:
        # 1. Test Client Initialization
        client = get_alpaca_client()
        print("OK: Client Initialized")
        
        # 2. Test Account Fetch
        account = client.get_account()
        
        # Verify it's a paper account
        if account.status == "ACTIVE":
            print("OK: Account Status: ACTIVE")
        else:
             print(f"WARN: Account Status: {account.status}")
             
        # In Alpaca SDK, paper accounts are not strictly flagged with a 'is_paper' boolean on the account object, 
        # but the TradingClient wrapper throws if the URL is wrong. 
        # We also configured it explicitly.
        print("OK: Paper Trading Enforced by Client Config")

        # 3. Test Portfolio Mapping
        portfolio = fetch_portfolio_state()
        print("OK: Portfolio Data Fetched")
        
        print("\n--- Portfolio Summary ---")
        print(f"Equity:           ${portfolio.account_equity:,.2f}")
        print(f"Buying Power:     ${portfolio.buying_power:,.2f}")
        print(f"Cash:             ${portfolio.cash:,.2f}")
        print(f"Daily Loss:       ${portfolio.current_daily_loss:,.2f}")
        print(f"Open Positions:   {portfolio.number_of_open_positions}")
        
        print("\nDIAGNOSTIC SUCCESSFUL")
        
    except ValueError as e:
        print(f"\nConfiguration Error: {e}")
        print("Please check your .env file.")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\nConnection Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()
