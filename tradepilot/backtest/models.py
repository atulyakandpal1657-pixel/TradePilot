"""
tradepilot/backtest/models.py

Dedicated backtest data models — completely isolated from the live trading
pipeline.  No instances of these classes are ever written to trade_records,
pending_proposals, or any other persistent store used by real trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


@dataclass
class BacktestTrade:
    """A single simulated round-trip trade (or an open position at end of backtest)."""
    symbol: str
    action: str                        # "BUY"
    quantity: float
    stop_loss: float

    entry_date: date
    entry_price: float                 # open price of bar[i+1] — no look-ahead

    exit_date: Optional[date] = None   # None if position is still open at backtest end
    exit_price: Optional[float] = None # open price of the exit bar
    exit_reason: Optional[str] = None  # "STOP_LOSS" | "END_OF_DATA" | None (still open)

    pnl: Optional[float] = None        # (exit_price - entry_price) * quantity
    pnl_pct: Optional[float] = None    # pnl / (entry_price * quantity)

    def is_open(self) -> bool:
        return self.exit_date is None


@dataclass
class BacktestEquityPoint:
    """Daily equity snapshot used to build the equity curve."""
    date: date
    cash: float
    position_value: float              # sum(qty * close_price) for open positions
    total_equity: float                # cash + position_value
    daily_return: float                # (total_equity - prev_equity) / prev_equity


@dataclass
class BacktestBenchmark:
    """
    Buy-and-hold benchmark performance for the same symbol and period.
    Comparison only — not a tradable strategy recommendation.
    """
    symbol: str
    initial_capital: float
    final_equity: float
    total_pnl: float
    total_return_pct: float
    start_price: float
    end_price: float


@dataclass
class BacktestResult:
    """Aggregate result returned by BacktestEngine.run()."""
    symbol: str
    start: date
    end: date
    strategy: str                      # human-readable name, e.g. "DeterministicBaseline"
    exit_rule: str                     # human-readable name, e.g. "CloseBasedStopLoss"

    initial_capital: float
    final_equity: float
    total_pnl: float                   # sum of realized pnl (open trades excluded)
    total_return_pct: float            # (final_equity - initial_capital) / initial_capital

    total_trades: int                  # closed trades only
    winning_trades: int
    losing_trades: int
    win_rate: float                    # winning / total_trades, or 0 if no trades
    max_drawdown: float                # maximum peak-to-trough decline in total_equity

    open_positions_at_end: int         # positions that were never exited
    bars_processed: int

    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[BacktestEquityPoint] = field(default_factory=list)

    # ── Step 9 Analytics & Performance Integrity ─────────────────────────────
    realized_pnl: float = 0.0          # realized P&L from closed trades (matches total_pnl)
    unrealized_pnl: float = 0.0        # mark-to-market P&L on positions open at end of data
    avg_winning_trade: Optional[float] = None  # average P&L per winning closed trade
    avg_losing_trade: Optional[float] = None   # average P&L per losing closed trade
    profit_factor: Optional[float] = None      # gross profit / gross loss (None if 0 losses)
    benchmark: Optional[BacktestBenchmark] = None # buy-and-hold benchmark

