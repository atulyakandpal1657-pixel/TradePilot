"""
tradepilot/backtest/data.py

Historical data adapter for the backtester.

Key design decisions:
- Fetches the full date range ONCE from Alpaca — never once per bar.
- Exposes fetch_historical_bars() which returns a plain pandas DataFrame.
- This is a read-only operation.  It writes nothing to the database.
- open, close, volume, timestamp are required columns.
- Enough rows are fetched before `start` to warm up SMA50 + volatility
  (at least 50 extra trading days of history pre-pended, then trimmed).
"""
from __future__ import annotations

from datetime import date, timedelta, timezone, datetime as dt
from typing import Optional
import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.common.exceptions import APIError

from tradepilot.core.config import settings

# Minimum number of trading days needed before the first decision bar
# to warm up SMA50 (50) + volatility (20) — use SMA50 as the binding constraint.
_WARMUP_BARS = 60  # slightly more than 50 to be safe after weekends/holidays


def _get_client() -> StockHistoricalDataClient:
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise ValueError("Alpaca credentials are not configured.")
    return StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )


def fetch_historical_bars(
    symbol: str,
    start: date,
    end: date,
    *,
    warmup_bars: int = _WARMUP_BARS,
    client: Optional[StockHistoricalDataClient] = None,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars for *symbol* from *start* to *end* (inclusive),
    prepending ~*warmup_bars* extra trading days so that SMA50 / volatility
    can be computed without NaN at the first decision bar.

    Returns a DataFrame with columns:
        timestamp (datetime, UTC, index)
        open, close, volume (float)
        sma_20, sma_50      (float, calculated here)
        daily_return        (float)
        volatility          (float, 20-day std of daily_return)
        price_change_pct    (float, single-day return vs previous close)
        previous_close      (float)

    The first *warmup_bars* rows exist only to seed the rolling windows;
    the caller (engine) must skip them when iterating decision bars.

    Raises:
        ValueError  — symbol unknown / insufficient history.
        ConnectionError — Alpaca API unreachable.
    """
    symbol = symbol.upper()
    if client is None:
        client = _get_client()

    # Extend the fetch window backwards to include the warmup period.
    # calendar days: warmup_bars * ~1.5 to account for weekends/holidays.
    fetch_start = dt.combine(start, dt.min.time()).replace(tzinfo=timezone.utc) \
                  - timedelta(days=warmup_bars * 2)
    fetch_end = dt.combine(end, dt.max.time()).replace(tzinfo=timezone.utc)

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=fetch_start,
        end=fetch_end,
        feed=DataFeed.IEX,
    )

    try:
        bars_response = client.get_stock_bars(req)
    except APIError as exc:
        raise ConnectionError(f"Failed to fetch historical data for {symbol}: {exc}") from exc

    if not bars_response.data or symbol not in bars_response.data:
        raise ValueError(f"No historical data returned for {symbol}.")

    raw = bars_response.data[symbol]
    df = pd.DataFrame([
        {"timestamp": b.timestamp, "open": b.open, "close": b.close, "volume": b.volume}
        for b in raw
    ])

    if df.empty:
        raise ValueError(f"Empty bar data returned for {symbol}.")

    df = df.sort_values("timestamp").reset_index(drop=True)

    # Calculate rolling indicators (using only past rows — no look-ahead)
    df["daily_return"]   = df["close"].pct_change()
    df["sma_20"]         = df["close"].rolling(window=20).mean()
    df["sma_50"]         = df["close"].rolling(window=50).mean()
    df["volatility"]     = df["daily_return"].rolling(window=20).std()
    df["previous_close"] = df["close"].shift(1)
    df["price_change_pct"] = df["daily_return"]  # alias for MarketSnapshot compatibility

    # Trim to the requested date range (keep warmup rows that precede `start`
    # by retaining rows where sma_50 is not NaN, which requires >= 50 bars).
    # The engine will only generate decisions for rows within [start, end].
    start_ts = dt.combine(start, dt.min.time()).replace(tzinfo=timezone.utc)
    end_ts   = dt.combine(end,   dt.max.time()).replace(tzinfo=timezone.utc)

    # Keep the full warmup window to avoid NaN at first decision bar.
    df = df[df["timestamp"] <= end_ts].copy()

    # Drop rows where SMA50 is NaN — they cannot produce a valid signal anyway.
    df = df.dropna(subset=["sma_50", "sma_20", "volatility", "previous_close"])
    df = df.reset_index(drop=True)

    if df.empty:
        raise ValueError(
            f"Insufficient history for {symbol}: need at least 50 trading days before {start}."
        )

    # Tag which rows fall inside the requested [start, end] window (decision rows).
    df["is_decision_bar"] = df["timestamp"] >= start_ts

    return df
