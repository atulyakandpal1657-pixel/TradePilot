from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.common.exceptions import APIError
from tradepilot.core.config import settings
from tradepilot.core.models import MarketSnapshot

def get_market_data_client() -> StockHistoricalDataClient:
    """Returns an initialized Alpaca StockHistoricalDataClient."""
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise ValueError("Alpaca credentials are not configured.")
    
    return StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key
    )

def fetch_market_snapshot(symbol: str) -> MarketSnapshot:
    """
    Fetches recent historical data for a symbol and calculates indicators.
    Returns a MarketSnapshot.
    """
    client = get_market_data_client()
    
    # We need at least 50 days of data for SMA50. Fetch 100 days just to be safe.
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=100)
    
    request_params = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date,
        feed=DataFeed.IEX
    )
    
    try:
        bars = client.get_stock_bars(request_params)
    except APIError as e:
        raise ConnectionError(f"Failed to fetch market data from Alpaca: {str(e)}")
        
    if not bars.data or symbol not in bars.data or len(bars.data[symbol]) < 50:
        raise ValueError(f"Insufficient historical data for {symbol}. At least 50 days required.")
        
    # Convert to DataFrame for easy calculation
    df = pd.DataFrame([
        {
            'close': bar.close,
            'volume': bar.volume,
            'timestamp': bar.timestamp
        }
        for bar in bars.data[symbol]
    ])
    
    # Sort by timestamp to ensure chronological order
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    # Calculate indicators
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['daily_return'] = df['close'].pct_change()
    df['volatility'] = df['daily_return'].rolling(window=20).std()
    
    # Get the latest data point
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    
    current_price = latest['close']
    previous_close = previous['close']
    price_change_pct = (current_price - previous_close) / previous_close
    
    return MarketSnapshot(
        symbol=symbol,
        current_price=float(current_price),
        timestamp=latest['timestamp'],
        volume=float(latest['volume']),
        previous_close=float(previous_close),
        price_change_pct=float(price_change_pct),
        sma_20=float(latest['sma_20']),
        sma_50=float(latest['sma_50']),
        volatility=float(latest['volatility'])
    )
