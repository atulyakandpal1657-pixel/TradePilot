"""
tradepilot/alpaca/trade_stream.py

Maps Alpaca trade-update events to TradeRecord lifecycle transitions.

Correlation ID contract
───────────────────────
  TradeProposal.trade_id
    == ApprovedTrade.trade_id
    == Alpaca client_order_id              (set in execution.py)
    == TradeRecord.trade_id                (journalled on submission)

  When Alpaca returns an order object, its own UUID is stored as
  TradeRecord.alpaca_order_id (informational only).

Position-close policy
─────────────────────
  A SELL order does NOT automatically close a trade.
  check_and_trigger_forensics() verifies the actual Alpaca position
  quantity is zero (or the position no longer exists) before allowing
  exit_price / realized_pnl to be written and forensics to be triggered.
"""

import datetime
import uuid
from typing import Optional

from tradepilot.core.models import ExecutionStatus, TradeRecord, TradeMemory, MarketSnapshot
from tradepilot.memory.journal import TradeJournal
from tradepilot.agents.forensics import ForensicsAgent
from tradepilot.memory.store import MemoryStore


class TradeStreamHandler:
    """
    Handles streaming trade-update events from Alpaca and persists lifecycle
    changes to the TradeJournal.  All methods are pure I/O — no risk logic.
    """

    def __init__(
        self,
        journal: TradeJournal,
        forensics_agent: Optional[ForensicsAgent],
        memory_store: Optional[MemoryStore],
    ):
        self.journal = journal
        self.forensics = forensics_agent
        self.store = memory_store

    # ── event routing ────────────────────────────────────────────────────────

    def handle_trade_update(self, event: dict) -> None:
        """
        Route an Alpaca trade-update event dict to the correct handler.

        Expected keys:
            event  – string event type ("new", "partial_fill", "fill", …)
            order  – dict with at least:
                        client_order_id, id, filled_qty, filled_avg_price
        """
        order = event.get("order", {})
        client_order_id = order.get("client_order_id", "")

        # Resolve our TradeRecord via the correlated UUID
        try:
            trade_id = uuid.UUID(client_order_id)
        except (ValueError, AttributeError):
            return  # Not our order — ignore

        record = self.journal.get_record(trade_id)
        if record is None:
            return  # Unknown trade — ignore

        # Store Alpaca's own order id once we have it
        alpaca_oid = order.get("id")
        if alpaca_oid and record.alpaca_order_id is None:
            record.alpaca_order_id = str(alpaca_oid)

        event_type = event.get("event", "")

        if event_type in ("new", "pending_new", "accepted"):
            self._on_accepted(record)

        elif event_type == "partial_fill":
            self._on_partial_fill(record, order)

        elif event_type == "fill":
            self._on_fill(record, order)

        elif event_type == "rejected":
            self._on_terminal(record, ExecutionStatus.REJECTED)

        elif event_type in ("canceled", "expired"):
            self._on_terminal(record, ExecutionStatus.CANCELLED)

    # ── event handlers ───────────────────────────────────────────────────────

    def _on_accepted(self, record: TradeRecord) -> None:
        record.execution_status = ExecutionStatus.ACCEPTED
        self.journal.save_record(record)

    def _on_partial_fill(self, record: TradeRecord, order: dict) -> None:
        """
        Update cumulative fill stats.

        Alpaca sends cumulative totals in every partial_fill event,
        so we can safely overwrite filled_quantity with the latest value.
        """
        raw_qty = order.get("filled_qty")
        raw_price = order.get("filled_avg_price")

        if raw_qty is not None:
            record.filled_quantity = float(raw_qty)

        if raw_price is not None:
            record.average_fill_price = float(raw_price)

        record.execution_status = ExecutionStatus.PARTIALLY_FILLED

        # Never overwrite requested quantity; remaining_quantity is computed
        self.journal.save_record(record)

    def _on_fill(self, record: TradeRecord, order: dict) -> None:
        """
        Complete fill: persist filled qty, average price, entry info.
        Does NOT calculate realized P&L — that requires a confirmed exit.
        """
        raw_qty = order.get("filled_qty")
        raw_price = order.get("filled_avg_price")

        if raw_qty is not None:
            record.filled_quantity = float(raw_qty)

        if raw_price is not None:
            record.average_fill_price = float(raw_price)
            record.entry_price = float(raw_price)  # canonical entry price

        record.entry_timestamp = datetime.datetime.now(datetime.timezone.utc)
        record.execution_status = ExecutionStatus.FILLED

        # Guard: quantity should never be overwritten
        self.journal.save_record(record)

    def _on_terminal(self, record: TradeRecord, status: ExecutionStatus) -> None:
        record.execution_status = status
        self.journal.save_record(record)

    # ── position-close / forensics hook ─────────────────────────────────────

    def check_and_trigger_forensics(
        self,
        record: TradeRecord,
        current_positions: dict,
        exit_price: Optional[float] = None,
        exit_snapshot: Optional[MarketSnapshot] = None,
    ) -> None:
        """
        Determine whether the tracked position is genuinely closed.

        A position is considered closed when:
          1. The Alpaca position for the symbol is absent OR has qty == 0, AND
          2. The trade was previously FILLED (i.e. it actually had an entry).

        Only after both conditions hold do we write exit data and trigger
        forensics.  We never fabricate exit_price — it must be supplied by
        the caller from a real Alpaca event or position query.

        A SELL order alone is NOT sufficient to declare the position closed;
        the caller must verify actual Alpaca position state.
        """
        if record.execution_status != ExecutionStatus.FILLED:
            return  # Nothing to close — trade was never actually filled

        # Check whether the Alpaca position is gone / zero
        pos = current_positions.get(record.symbol)
        position_is_zero = (pos is None) or (float(pos.qty) == 0)

        if not position_is_zero:
            return  # Position still open — do not write exit data

        if exit_price is None:
            return  # Caller hasn't supplied a real exit price — do not fabricate

        # Populate exit fields
        record.exit_price = exit_price
        record.exit_timestamp = datetime.datetime.now(datetime.timezone.utc)

        # Realized P&L: only now that entry AND exit are confirmed
        if record.entry_price is not None:
            raw_pnl = (exit_price - record.entry_price) * record.filled_quantity
            record.realized_pnl = raw_pnl if record.action.value == "BUY" else -raw_pnl
            if record.entry_price > 0:
                ret = (exit_price - record.entry_price) / record.entry_price
                record.realized_return_pct = ret if record.action.value == "BUY" else -ret

        self.journal.save_record(record)

        # Trigger Forensics → TradeMemory if agents are wired up
        if self.forensics is None or self.store is None:
            return

        forensics = self.forensics.analyze_trade(record, exit_snapshot=exit_snapshot)
        if forensics is None:
            return

        memory = TradeMemory(
            source_trade_id=record.trade_id,
            symbol=record.symbol,
            market_condition=forensics.actual_market_conditions,
            lesson=forensics.lesson,
            applicable_conditions=(
                forensics.successful_assumptions + forensics.failed_assumptions
            ),
            evidence_count=1,
            confidence=forensics.confidence,
        )
        self.store.save_memory(memory)
