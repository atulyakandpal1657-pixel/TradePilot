from tradepilot.core.models import PortfolioState
from tradepilot.alpaca.client import get_alpaca_client
from alpaca.common.exceptions import APIError
from typing import Optional

def fetch_portfolio_state() -> PortfolioState:
    """
    Fetches the current Alpaca paper account and converts it into a PortfolioState model.
    """
    client = get_alpaca_client()
    
    try:
        account = client.get_account()
        positions = client.get_all_positions()
    except APIError as e:
        raise ConnectionError(f"Failed to fetch portfolio data from Alpaca: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error communicating with Alpaca: {str(e)}")

    # Extract positions (long quantities only; short selling is not supported in the MVP)
    open_positions = [pos.symbol for pos in positions]
    position_quantities = {pos.symbol: float(pos.qty) for pos in positions}
    number_of_open_positions = len(open_positions)
    
    # Extract financial metrics (ensure we convert string outputs to floats)
    account_equity = float(account.equity)
    buying_power = float(account.buying_power)
    cash = float(account.cash)
    
    # Current daily loss calculation (equity vs last_equity is roughly daily PnL)
    last_equity = float(account.last_equity)
    daily_pnl = account_equity - last_equity
    current_daily_loss = -daily_pnl if daily_pnl < 0 else 0.0

    return PortfolioState(
        account_equity=account_equity,
        buying_power=buying_power,
        cash=cash,
        current_daily_loss=current_daily_loss,
        open_positions=open_positions,
        number_of_open_positions=number_of_open_positions,
        position_quantities=position_quantities,
    )
