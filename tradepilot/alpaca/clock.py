from tradepilot.alpaca.client import get_alpaca_client
from tradepilot.core.models import MarketStatus

def is_market_open() -> MarketStatus:
    """
    Fetches the current market clock from Alpaca.
    Returns a MarketStatus containing is_open, timestamp, next_open, and next_close.
    """
    client = get_alpaca_client()
    clock = client.get_clock()
    
    return MarketStatus(
        is_open=clock.is_open,
        timestamp=clock.timestamp,
        next_open=clock.next_open,
        next_close=clock.next_close
    )
