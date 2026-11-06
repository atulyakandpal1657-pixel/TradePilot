from tradepilot.core.models import PortfolioState

def calculate_position_size(current_price: float, portfolio: PortfolioState, max_pct: float) -> float:
    """
    Calculates a safe position size based on account equity, NOT buying power.
    This is deterministic application-level sizing.
    """
    max_notional = portfolio.account_equity * max_pct
    # Round down to nearest whole share
    quantity = int(max_notional / current_price)
    return float(quantity)
