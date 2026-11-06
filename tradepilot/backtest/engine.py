"""
tradepilot/backtest/engine.py

Deterministic daily-bar backtester.

Guarantees:
- No writes to trade_records or pending_proposals.
- No Alpaca order submissions.
- No Gemini / LLM calls.
- same historical data + same parameters → same result (bit-for-bit).

Decision-fill timing:
    Bar i  : strategy sees close[i], sma[i], volume[i] — makes a BUY/NO_TRADE decision.
    Bar i+1: entry fills at open[i+1]  (no look-ahead into i+1 close or beyond).

Exit rule (CloseBasedStopLossExit):
    At end of bar i, if a held position's close[i] < stop_loss:
        → schedule exit.
    Bar i+1: exit fills at open[i+1].

    IMPORTANT: This is a simplified daily-bar simulation.  It does NOT model
    intraday stop-order triggers.  A real position might breach its stop loss
    intraday and fill at a price worse than open[i+1].  The model presented
    here is optimistic in that regard.

    Positions that are never stopped out by the last bar are left open and
    reported explicitly.  No fabricated exit price is assigned.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
import pandas as pd

from tradepilot.agents.strategy import DeterministicBaselineStrategy, StrategyAgent
from tradepilot.core.models import (
    MarketSnapshot, PortfolioState
)
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.sizing import calculate_position_size
from tradepilot.core.config import settings

from tradepilot.backtest.models import (
    BacktestBenchmark, BacktestEquityPoint, BacktestResult, BacktestTrade
)
from tradepilot.backtest.portfolio import SimulatedPortfolio


class BacktestEngine:
    """
    Run a deterministic daily-bar backtest.

    Parameters
    ----------
    initial_capital : float
        Starting cash.  Position sizing is derived from this via calculate_position_size().
    strategy : StrategyAgent, optional
        Defaults to DeterministicBaselineStrategy.  Do NOT pass LLMStrategyAgent here.
    risk_params : dict, optional
        Overrides for RiskEngine parameters.  Keys:
            max_position_pct, max_risk_usd, max_open_positions, daily_loss_limit
    max_pct : float, optional
        Passed to calculate_position_size as max_pct.
        Defaults to settings.max_position_pct.
    """

    EXIT_RULE_NAME = "CloseBasedStopLoss"

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        strategy: Optional[StrategyAgent] = None,
        risk_params: Optional[dict] = None,
        max_pct: Optional[float] = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.strategy = strategy or DeterministicBaselineStrategy()
        self.max_pct = max_pct if max_pct is not None else settings.max_position_pct

        rp = risk_params or {}
        self.risk_engine = RiskEngine(
            max_position_pct=rp.get("max_position_pct", settings.max_position_pct),
            max_risk_usd=rp.get("max_risk_usd", settings.max_risk_usd),
            max_open_positions=rp.get("max_open_positions", settings.max_open_positions),
            daily_loss_limit=rp.get("daily_loss_limit", settings.daily_loss_limit),
        )

    # ── Public interface ───────────────────────────────────────────────────────

    def run(
        self,
        symbol: str,
        df: pd.DataFrame,
        start: date,
        end: date,
    ) -> BacktestResult:
        """
        Execute the backtest on pre-fetched *df*.

        *df* must be produced by fetch_historical_bars() or a compatible synthetic
        DataFrame that includes columns:
            timestamp, open, close, volume,
            sma_20, sma_50, volatility, price_change_pct, previous_close,
            is_decision_bar (bool)

        The engine ONLY makes decisions on rows where is_decision_bar == True.
        Warmup rows (is_decision_bar == False) are used for indicator continuity
        but generate no trades.
        """
        portfolio = SimulatedPortfolio(initial_capital=self.initial_capital)

        completed_trades: list[BacktestTrade] = []
        equity_curve: list[BacktestEquityPoint] = []
        pending_entries: dict[str, dict] = {}   # symbol → {quantity, stop_loss, entry_date}
        pending_exits: dict[str, str] = {}       # symbol → exit_reason

        prev_equity = self.initial_capital
        bars_processed = 0

        df = df.reset_index(drop=True)
        n = len(df)

        for i, row in df.iterrows():
            # ── Reset daily P&L at the start of each simulated day ───────────
            portfolio.reset_daily_pnl()

            bar_date = row["timestamp"].date() if hasattr(row["timestamp"], "date") else row["timestamp"]
            price_map = {symbol: float(row["close"])}

            # ── 1. Execute pending ENTRIES (filled at today's open) ───────────
            for sym, entry_info in list(pending_entries.items()):
                fill_price = float(row["open"])
                # Verify we still have enough cash at fill time
                cost = entry_info["quantity"] * fill_price
                if cost > portfolio.cash:
                    # Cannot fill — skip this entry (cost may differ from decision bar)
                    pending_entries.pop(sym)
                    continue
                portfolio.open_position(
                    symbol=sym,
                    quantity=entry_info["quantity"],
                    fill_price=fill_price,
                    stop_loss=entry_info["stop_loss"],
                    entry_date=entry_info["entry_date"],
                )
                pending_entries.pop(sym)

            # ── 2. Execute pending EXITS (filled at today's open) ─────────────
            for sym, exit_reason in list(pending_exits.items()):
                fill_price = float(row["open"])
                pos = portfolio.get_position(sym)
                if pos is None:
                    pending_exits.pop(sym)
                    continue
                pnl, pnl_pct = portfolio.close_position(sym, fill_price)
                completed_trades.append(BacktestTrade(
                    symbol=sym,
                    action="BUY",
                    quantity=pos.quantity,
                    stop_loss=pos.stop_loss,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=bar_date,
                    exit_price=fill_price,
                    exit_reason=exit_reason,
                    pnl=round(pnl, 6),
                    pnl_pct=round(pnl_pct, 6),
                ))
                pending_exits.pop(sym)

            # ── 3. Strategy decision (only on decision bars) ──────────────────
            if row.get("is_decision_bar", True):
                bars_processed += 1
                snapshot = self._build_snapshot(symbol, row)
                port_state: PortfolioState = portfolio.as_portfolio_state(price_map)

                proposal = self.strategy.generate_proposal(snapshot, port_state)

                if proposal is not None and not portfolio.has_position(symbol):
                    # Run risk engine
                    decision = self.risk_engine.evaluate(proposal, port_state)
                    if decision.status.value == "APPROVED":
                        # Schedule entry for NEXT bar (look-ahead protection)
                        if i + 1 < n:
                            pending_entries[symbol] = {
                                "quantity": proposal.quantity,
                                "stop_loss": proposal.stop_loss or 0.0,
                                "entry_date": bar_date,
                            }

                # ── 4. Stop-loss check (CloseBasedStopLossExit) ───────────────
                # Check at end of today's bar, using today's CLOSE.
                # If triggered, schedule exit at NEXT bar's open.
                pos = portfolio.get_position(symbol)
                if pos is not None and pos.stop_loss > 0:
                    if float(row["close"]) < pos.stop_loss:
                        if i + 1 < n:
                            pending_exits[symbol] = "STOP_LOSS"
                        # else: no next bar — handled as open position at end

            # ── 5. Equity snapshot ────────────────────────────────────────────
            total_eq = portfolio.total_equity(price_map)
            daily_ret = (total_eq - prev_equity) / prev_equity if prev_equity else 0.0
            equity_curve.append(BacktestEquityPoint(
                date=bar_date,
                cash=round(portfolio.cash, 4),
                position_value=round(portfolio.position_value(price_map), 4),
                total_equity=round(total_eq, 4),
                daily_return=round(daily_ret, 6),
            ))
            prev_equity = total_eq

        # ── 6. Mark open positions at end of data ────────────────────────────
        for sym, pos in portfolio.open_positions.items():
            completed_trades.append(BacktestTrade(
                symbol=sym,
                action="BUY",
                quantity=pos.quantity,
                stop_loss=pos.stop_loss,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                exit_date=None,
                exit_price=None,
                exit_reason="END_OF_DATA",
                pnl=None,
                pnl_pct=None,
            ))

        # ── 7. Compute metrics ────────────────────────────────────────────────
        closed = [t for t in completed_trades if t.exit_reason != "END_OF_DATA" and t.pnl is not None]
        open_at_end = [t for t in completed_trades if t.exit_reason == "END_OF_DATA"]

        winning = [t for t in closed if (t.pnl or 0) > 0]
        losing  = [t for t in closed if (t.pnl or 0) <= 0]
        strictly_losing = [t for t in closed if (t.pnl or 0) < 0]

        total_pnl = sum(t.pnl for t in closed)
        realized_pnl = total_pnl

        # Calculate unrealized P&L on open positions at the end of data
        last_close = float(df.iloc[-1]["close"]) if len(df) > 0 else 0.0
        last_price_map = {symbol: last_close}
        open_cost_basis = sum(pos.quantity * pos.entry_price for pos in portfolio.open_positions.values())
        open_market_val = portfolio.position_value(last_price_map)
        unrealized_pnl = open_market_val - open_cost_basis

        final_equity = equity_curve[-1].total_equity if equity_curve else self.initial_capital
        total_return_pct = (final_equity - self.initial_capital) / self.initial_capital
        win_rate = len(winning) / len(closed) if closed else 0.0
        max_dd = self._max_drawdown([pt.total_equity for pt in equity_curve])

        # Step 9 Analytics: Average Win / Loss and Profit Factor
        avg_winning_trade = round(sum(t.pnl for t in winning) / len(winning), 4) if winning else None
        losing_for_avg = strictly_losing if strictly_losing else losing
        avg_losing_trade = round(sum(t.pnl for t in losing_for_avg) / len(losing_for_avg), 4) if losing_for_avg else None

        gross_profits = sum(t.pnl for t in winning)
        gross_losses = abs(sum(t.pnl for t in strictly_losing))
        if gross_losses > 0:
            profit_factor = round(gross_profits / gross_losses, 4)
        else:
            profit_factor = None  # None if gross losses == 0 (no losses or no trades)

        # Step 9 Buy-and-hold benchmark
        benchmark = self.calculate_buy_and_hold_benchmark(symbol, df, self.initial_capital)

        strategy_name = type(self.strategy).__name__

        return BacktestResult(
            symbol=symbol,
            start=start,
            end=end,
            strategy=strategy_name,
            exit_rule=self.EXIT_RULE_NAME,
            initial_capital=self.initial_capital,
            final_equity=round(final_equity, 4),
            total_pnl=round(total_pnl, 4),
            total_return_pct=round(total_return_pct, 6),
            total_trades=len(closed),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            max_drawdown=round(max_dd, 6),
            open_positions_at_end=len(open_at_end),
            bars_processed=bars_processed,
            trades=completed_trades,
            equity_curve=equity_curve,
            realized_pnl=round(realized_pnl, 4),
            unrealized_pnl=round(unrealized_pnl, 4),
            avg_winning_trade=avg_winning_trade,
            avg_losing_trade=avg_losing_trade,
            profit_factor=profit_factor,
            benchmark=benchmark,
        )

    # ── Benchmark helper ───────────────────────────────────────────────────────

    @staticmethod
    def calculate_buy_and_hold_benchmark(
        symbol: str,
        df: pd.DataFrame,
        initial_capital: float,
    ) -> Optional[BacktestBenchmark]:
        """
        Calculate buy-and-hold benchmark return over the same period and historical data.

        Methodology:
        - Uses 100% of initial_capital allocated at the Open of the first decision bar.
        - Fractional shares supported: shares = initial_capital / first_bar_open.
        - Final equity evaluated at the Close of the last decision bar:
          final_equity = shares * last_bar_close.
        - Total return pct = (last_bar_close - first_bar_open) / first_bar_open.
        - Benchmark is for comparison only, not a tradable strategy recommendation.
        """
        if df is None or df.empty:
            return None

        if "is_decision_bar" in df.columns:
            decision_df = df[df["is_decision_bar"] == True]
        else:
            decision_df = df

        if decision_df.empty:
            return None

        first_bar = decision_df.iloc[0]
        last_bar = decision_df.iloc[-1]

        try:
            start_price = float(first_bar["open"])
            end_price = float(last_bar["close"])
        except (KeyError, ValueError, TypeError):
            return None

        if start_price <= 0:
            return None

        shares = initial_capital / start_price
        final_equity = shares * end_price
        total_pnl = final_equity - initial_capital
        total_return_pct = (end_price - start_price) / start_price

        return BacktestBenchmark(
            symbol=symbol,
            initial_capital=round(initial_capital, 4),
            final_equity=round(final_equity, 4),
            total_pnl=round(total_pnl, 4),
            total_return_pct=round(total_return_pct, 6),
            start_price=round(start_price, 4),
            end_price=round(end_price, 4),
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _build_snapshot(symbol: str, row: pd.Series) -> MarketSnapshot:
        """
        Build a MarketSnapshot from a single DataFrame row.

        All values come from row[i] — no future data is accessed here.
        This is called BEFORE looking at row[i+1] for fills.
        """
        return MarketSnapshot(
            symbol=symbol,
            current_price=float(row["close"]),
            timestamp=row["timestamp"],
            volume=float(row["volume"]),
            previous_close=float(row["previous_close"]),
            price_change_pct=float(row["price_change_pct"]),
            sma_20=float(row["sma_20"]),
            sma_50=float(row["sma_50"]),
            volatility=float(row["volatility"]),
        )

    @staticmethod
    def _max_drawdown(equity_values: list[float]) -> float:
        """Maximum peak-to-trough decline (as a negative fraction, e.g. -0.05 = -5%)."""
        if not equity_values:
            return 0.0
        peak = equity_values[0]
        max_dd = 0.0
        for eq in equity_values:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak if peak else 0.0
            if dd < max_dd:
                max_dd = dd
        return max_dd

