import os
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError
from tradepilot.core.config import settings

def get_alpaca_client() -> TradingClient:
    """
    Returns an initialized Alpaca TradingClient.
    Enforces PAPER trading configuration.
    """
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise ValueError("Alpaca credentials are not configured.")
        
    if not settings.alpaca_paper_trade:
        raise ValueError("LIVE trading is prohibited in this phase. alpaca_paper_trade must be True.")

    try:
        # Forcing paper trading
        client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=True
        )
        return client
    except Exception as e:
        raise ConnectionError(f"Failed to initialize Alpaca client: {str(e)}")
