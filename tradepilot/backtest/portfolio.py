"""
tradepilot/backtest/portfolio.py

Pure-Python simulated portfolio.

Constraints:
- No database writes.
- No Alpaca API calls.
- No Gemini calls.
- buying_power == cash (no margin).
- Reuses calculate_position_size() from tradepilot.core.sizing.
- Reuses PortfolioState from tradepilot.core.models so that RiskEngine.evaluate()
  can be called without modification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from tradepilot.core.models import PortfolioState


@dataclass
class _OpenPosition:
    """A position that has been entered but not yet exited."""
    symbol: str
    quantity: float
    entry_price: float
    stop_loss: float
    entry_date: date


@dataclass
class SimulatedPortfolio:
    """
    Mutable simulated account state.

    All values start from initial_capital held entirely in cash.
    buying_power is always equal to cash (no margin, first version).
    """
    initial_capital: float
    cash: float = field(init=False)
    realized_pnl: float = field(init=False, default=0.0)
    daily_pnl: float = field(init=False, default=0.0)   # reset each simulated day
    _positions: Dict[str, _OpenPosition] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.initial_capital

    # ── Snapshot for RiskEngine ───────────────────────────────────────────────

    def as_portfolio_state(self, price_map: Dict[str, float]) -> PortfolioState:
        """
        Build a PortfolioState compatible with RiskEngine.evaluate().

        *price_map* maps symbol → current close price (used to value positions).
        buying_power == cash (no margin).
        """
        position_value = sum(
            p.quantity * price_map.get(p.symbol, p.entry_price)
            for p in self._positions.values()
        )
        equity = self.cash + position_value
        return PortfolioState(
            account_equity=equity,
            buying_power=self.cash,       # buying_power = cash, no margin
            cash=self.cash,
            current_daily_loss=max(0.0, -self.daily_pnl),
            open_positions=list(self._positions.keys()),
            number_of_open_positions=len(self._positions),
            position_quantities={sym: p.quantity for sym, p in self._positions.items()},
        )

    # ── Trade actions ──────────────────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        quantity: float,
        fill_price: float,
        stop_loss: float,
        entry_date: date,
    ) -> None:
        """Deduct cash and record the new position."""
        cost = quantity * fill_price
        if cost > self.cash:
            raise ValueError(
                f"Insufficient cash to open {symbol}: need {cost:.2f}, have {self.cash:.2f}"
            )
        self.cash -= cost
        self._positions[symbol] = _OpenPosition(
            symbol=symbol,
            quantity=quantity,
            entry_price=fill_price,
            stop_loss=stop_loss,
            entry_date=entry_date,
        )

    def close_position(
        self,
        symbol: str,
        fill_price: float,
    ) -> tuple[float, float]:
        """
        Close *symbol* at *fill_price*.  Returns (pnl, pnl_pct).
        Raises KeyError if symbol is not held.
        """
        pos = self._positions.pop(symbol)
        proceeds = pos.quantity * fill_price
        cost_basis = pos.quantity * pos.entry_price
        pnl = proceeds - cost_basis
        pnl_pct = pnl / cost_basis if cost_basis else 0.0
        self.cash += proceeds
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        return pnl, pnl_pct

    def reset_daily_pnl(self) -> None:
        self.daily_pnl = 0.0

    # ── Read-only helpers ──────────────────────────────────────────────────────

    @property
    def open_positions(self) -> Dict[str, _OpenPosition]:
        return dict(self._positions)

    def position_value(self, price_map: Dict[str, float]) -> float:
        return sum(
            p.quantity * price_map.get(p.symbol, p.entry_price)
            for p in self._positions.values()
        )

    def total_equity(self, price_map: Dict[str, float]) -> float:
        return self.cash + self.position_value(price_map)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def get_position(self, symbol: str) -> Optional[_OpenPosition]:
        return self._positions.get(symbol)
