"""
tradepilot/decision_trace.py

Explainable Decision Trace — read-only CLI.

Assembles one complete decision trace across all 19 checkpoints:
  Market data/evidence -> Strategy -> Confidence -> Thesis -> Assumptions
  -> Invalidation -> Risk -> Stop -> Position sizing -> Demo sizing
  -> Human confirmation -> Client order ID -> Alpaca order ID
  -> Order status -> Filled qty -> Fill price -> Outcome -> Forensics -> Memory

Usage:
    uv run python -m tradepilot.decision_trace <alpaca_order_id>

SAFETY: This module is read-only. It NEVER submits, modifies,
or cancels any Alpaca order. It does NOT weaken RiskEngine rules.
"""

from __future__ import annotations

import argparse
import enum
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from typing import Optional

from tradepilot.alpaca.client import get_alpaca_client
from tradepilot.core.config import settings
from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore


def _sep(title: str = "", width: int = 70) -> str:
    if title:
        pad = width - len(title) - 4
        return f"-- {title} {'-' * max(pad, 2)}"
    return "-" * width


def _wrap(text: str, indent: int = 4, width: int = 70) -> str:
    prefix = " " * indent
    return textwrap.fill(
        text, width=width,
        initial_indent=prefix,
        subsequent_indent=prefix,
    )


def _bullet_list(items: list, indent: int = 4) -> str:
    prefix = " " * indent
    return "\n".join(f"{prefix}* {item}" for item in items)


def _fetch_alpaca_order(alpaca_order_id: str):
    try:
        client = get_alpaca_client()
        return client.get_order_by_id(alpaca_order_id)
    except Exception:
        return None


def _fetch_memories(symbol: str, source_trade_id: uuid.UUID) -> list:
    try:
        store = MemoryStore()
        all_mems = store.retrieve_memories(symbol, limit=10)
        linked = [m for m in all_mems if m.source_trade_id == source_trade_id]
        return linked if linked else all_mems[:3]
    except Exception:
        return []



def get_trace_data(
    journal: TradeJournal,
    alpaca_order_id: Optional[str] = None,
    trade_id_str: Optional[str] = None,
) -> Optional[dict]:
    """
    Extracts and computes all data necessary for the decision trace.
    Returns a dictionary of the structured trace data, or None if the record cannot be found.
    """
    record = None

    # --- Strategy 1: caller supplied an explicit trade_id string -----------
    if trade_id_str:
        try:
            record = journal.get_record(uuid.UUID(trade_id_str))
        except ValueError:
            pass

    # --- Strategy 2: caller supplied an alpaca_order_id -------------------
    if record is None and alpaca_order_id:
        # 2a. direct journal lookup by alpaca_order_id field
        record = journal.get_record_by_alpaca_order_id(alpaca_order_id)

    if record is None and alpaca_order_id:
        # 2b. the supplied id might actually be the trade_id (= client_order_id)
        try:
            record = journal.get_record(uuid.UUID(alpaca_order_id))
        except ValueError:
            pass

    if record is None and alpaca_order_id:
        # 2c. fetch from Alpaca and correlate via client_order_id
        order = _fetch_alpaca_order(alpaca_order_id)
        if order is not None:
            client_oid = getattr(order, "client_order_id", None)
            if client_oid:
                try:
                    record = journal.get_record(uuid.UUID(str(client_oid)))
                except ValueError:
                    pass

    if record is None:
        return None

    resolve_id = alpaca_order_id or record.alpaca_order_id
    live_order = _fetch_alpaca_order(resolve_id) if resolve_id else None

    def _order_val(attr, fallback=None):
        if live_order is None:
            return fallback
        val = getattr(live_order, attr, fallback)
        # Only dereference .value for genuine enum members (not MagicMock objects)
        if isinstance(val, enum.Enum):
            val = val.value
        return val

    live_status     = _order_val("status")
    live_filled_qty = _order_val("filled_qty")
    live_fill_price = _order_val("filled_avg_price")
    live_submitted  = _order_val("submitted_at")
    alpaca_oid_live = _order_val("id", record.alpaca_order_id)
    client_oid_live = _order_val("client_order_id", str(record.trade_id))

    memories = _fetch_memories(record.symbol, record.trade_id)

    thesis_raw   = record.original_thesis or ""
    thesis_parts = thesis_raw.split("\n\nEvidence:\n")
    thesis_text  = thesis_parts[0].strip()
    rest         = thesis_parts[1] if len(thesis_parts) > 1 else ""
    evidence_parts = rest.split("\n\nSummary:\n")
    evidence_text  = evidence_parts[0].strip()
    summary_text   = evidence_parts[1].strip() if len(evidence_parts) > 1 else ""
    memory_reasoning = ""
    if "\n\nMemory Reasoning:" in thesis_raw:
        memory_reasoning = thesis_raw.split("\n\nMemory Reasoning:")[-1].strip()

    demo_cap = settings.max_demo_order_notional

    # Snapshot price: price at which the strategy proposed the trade.
    # Parsed from the Evidence section of the thesis; NOT the fill price.
    snapshot_price: Optional[float] = None
    if evidence_text:
        import re as _re
        m = _re.search(r"Price:\s*([\d.]+)", evidence_text)
        if m:
            try:
                snapshot_price = float(m.group(1))
            except ValueError:
                pass

    # Fill price: actual average price from Alpaca / journal.
    actual_fill_price: Optional[float] = None
    raw_fill = live_fill_price if live_fill_price is not None else record.average_fill_price
    if raw_fill is not None:
        try:
            actual_fill_price = float(raw_fill)
        except (TypeError, ValueError):
            pass

    # For demo-note, use snapshot price so it reflects what the cap was
    # computed against at proposal time, not the post-fill price.
    demo_ref_price = snapshot_price or actual_fill_price
    if demo_ref_price and demo_ref_price > 0:
        demo_note = (
            f"Demo cap $%.2f / snapshot price $%.2f => max ~%.4f shares. "
            f"Submitted qty: %s"
        ) % (demo_cap, demo_ref_price, demo_cap / demo_ref_price, record.quantity)
    else:
        demo_note = f"Demo cap: ${demo_cap:.2f}. Submitted qty: {record.quantity}"

    if record.exit_price is not None and record.entry_price is not None:
        outcome_line = (
            f"CLOSED | Entry ${record.entry_price:.2f} -> Exit ${record.exit_price:.2f} | "
            f"P&L ${record.realized_pnl or 0:.2f} ({(record.realized_return_pct or 0)*100:.2f}%)"
        )
    elif record.entry_price is not None:
        fill_p = float(live_fill_price) if live_fill_price is not None else record.entry_price
        outcome_line = f"OPEN | Entry ${fill_p:.2f} -- position not yet closed"
    else:
        qty_str = live_filled_qty if live_filled_qty is not None else record.filled_quantity
        outcome_line = (
            f"Status: {live_status or record.execution_status.value} | Filled qty: {qty_str}"
        )

    # ── Evidence Audit (Step 10) ──────────────────────────────────────────────
    # Only use an audit that was genuinely persisted against the original snapshot.
    # IMPORTANT: Do NOT reconstruct a synthetic MarketSnapshot from thesis text and
    # audit the thesis against its own data — that is a circular self-validation
    # that produces a false PASSED result. If no real audit exists, report
    # MISSING_EVIDENCE honestly.
    evidence_audit = journal.get_evidence_audit_by_trade_id(record.trade_id)
    if evidence_audit is None:
        evidence_audit = getattr(record, "evidence_audit", None)
    if evidence_audit is None:
        from tradepilot.agents.evidence_auditor import EvidenceAuditor
        auditor = EvidenceAuditor()
        # No persisted real snapshot available — report MISSING_EVIDENCE.
        evidence_audit = auditor.audit(record, snapshot=None, symbol=record.symbol)

    return {
        "record": record,
        "live_status": live_status,
        "live_filled_qty": live_filled_qty,
        "live_fill_price": live_fill_price,
        "live_submitted": live_submitted,
        "alpaca_oid_live": alpaca_oid_live,
        "client_oid_live": client_oid_live,
        "memories": memories,
        "thesis_text": thesis_text,
        "evidence_text": evidence_text,
        "summary_text": summary_text,
        "memory_reasoning": memory_reasoning,
        "demo_cap": demo_cap,
        "snapshot_price": snapshot_price,
        "actual_fill_price": actual_fill_price,
        "demo_note": demo_note,
        "outcome_line": outcome_line,
        "evidence_audit": evidence_audit,
    }


def build_trace(
    journal: TradeJournal,
    alpaca_order_id: Optional[str] = None,
    trade_id_str: Optional[str] = None,
) -> int:
    data = get_trace_data(journal, alpaca_order_id, trade_id_str)

    if data is None:
        print(
            "ERROR: No TradeRecord found in journal for the supplied identifier.",
            file=sys.stderr,
        )
        return 1

    record = data["record"]
    live_status = data["live_status"]
    live_filled_qty = data["live_filled_qty"]
    live_fill_price = data["live_fill_price"]
    live_submitted = data["live_submitted"]
    alpaca_oid_live = data["alpaca_oid_live"]
    client_oid_live = data["client_oid_live"]
    memories = data["memories"]
    thesis_text = data["thesis_text"]
    evidence_text = data["evidence_text"]
    summary_text = data["summary_text"]
    memory_reasoning = data["memory_reasoning"]
    demo_cap = data["demo_cap"]
    snapshot_price = data["snapshot_price"]
    actual_fill_price = data["actual_fill_price"]
    demo_note = data["demo_note"]
    outcome_line = data["outcome_line"]

    W = "=" * 70
    print()
    print(W)
    print("  TRADEPILOT -- EXPLAINABLE DECISION TRACE")
    print(W)
    print(f"  Symbol : {record.symbol}")
    print(f"  Action : {record.action.value}")
    print(f"  Trade  : {record.trade_id}")
    print(W)

    print()
    print(_sep("1 . MARKET DATA / EVIDENCE"))
    if evidence_text:
        print(_wrap(evidence_text))
    else:
        print("    (evidence not captured separately in this record)")

    print()
    print(_sep("2 . STRATEGY DECISION"))
    print(f"    Action proposed by AI strategy agent: {record.action.value}")

    print()
    print(_sep("3 . CONFIDENCE"))
    print(f"    {record.original_confidence * 100:.0f}%")

    print()
    print(_sep("4 . THESIS"))
    print(_wrap(thesis_text or "(thesis not stored)"))
    if summary_text:
        print()
        print(_wrap(f"Summary: {summary_text}"))

    print()
    print(_sep("5 . ASSUMPTIONS"))
    if record.assumptions:
        print(_bullet_list(record.assumptions))
    else:
        print("    (none recorded)")

    print()
    print(_sep("6 . INVALIDATION CONDITIONS"))
    if record.invalidation_conditions:
        print(_bullet_list(record.invalidation_conditions))
    else:
        print("    (none recorded)")

    print()
    print(_sep("7 . RISK DECISION"))
    print(f"    RiskEngine verdict: {record.risk_decision.value}")
    print("    (Evaluated: max_position_pct, buying_power,")
    print("     max_open_positions, daily_loss_limit, stop-loss risk)")

    print()
    print(_sep("8 . STOP PRICE"))
    stop_hint = next(
        (c for c in record.invalidation_conditions if "sma" in c.lower() or "$" in c.lower()),
        None,
    )
    if stop_hint:
        print(f"    Derived from invalidation: {stop_hint}")
    else:
        print("    (stop price not separately recorded; see invalidation conditions above)")

    print()
    print(_sep("9 . POSITION SIZING (strategy proposal)"))
    print(f"    Strategy-proposed qty : {record.quantity} shares")
    if snapshot_price:
        print(f"    Snapshot price        : ${snapshot_price:.2f}  (market price when strategy ran)")
        print(f"    Proposed notional     : ${record.quantity * snapshot_price:.2f}")
    else:
        print("    (snapshot price not separately recorded)")

    print()
    print(_sep("10 . DEMO SIZING ($100 cap)"))
    print(f"    {demo_note}")
    print(f"    Submitted qty (after cap) : {record.quantity} shares")

    print()
    print(_sep("11 . HUMAN CONFIRMATION"))
    print("    Manual [y/N] prompt shown before submission.")
    print("    Alpaca order submitted only after explicit human approval.")
    print("    Autonomous trading is DISABLED in TradePilot.")

    print()
    print(_sep("12 . CLIENT ORDER ID"))
    print(f"    {client_oid_live}")
    print("    (TradePilot trade_id == Alpaca client_order_id -- correlation key)")

    print()
    print(_sep("13 . ALPACA ORDER ID"))
    if alpaca_oid_live:
        print(f"    {alpaca_oid_live}")
    else:
        print("    (not yet recorded)")

    print()
    print(_sep("14 . ORDER STATUS"))
    status_display = live_status or record.execution_status.value
    print(f"    {status_display}")
    if live_submitted:
        print(f"    Submitted at: {live_submitted}")

    print()
    print(_sep("15 . SUBMITTED QUANTITY"))
    print(f"    Submitted qty : {record.quantity} shares  (demo-capped)")

    print()
    print(_sep("16 . EXECUTION RESULT (actual fill from Alpaca)"))
    actual_filled_qty = live_filled_qty if live_filled_qty is not None else record.filled_quantity
    print(f"    Filled qty    : {actual_filled_qty} shares")
    if actual_fill_price is not None:
        fill_notional = float(actual_filled_qty) * actual_fill_price
        print(f"    Fill price    : ${actual_fill_price:.2f}  (average fill from Alpaca)")
        print(f"    Fill notional : ${fill_notional:.2f}")
        if snapshot_price and snapshot_price != actual_fill_price:
            price_diff = actual_fill_price - snapshot_price
            print(
                f"    vs. snapshot  : ${snapshot_price:.2f}  "
                f"(diff: {price_diff:+.2f}; market moved between proposal and fill)"
            )
    else:
        print("    Fill price    : (not yet filled)")

    print()
    print(_sep("17 . POSITION / OUTCOME"))
    print(f"    {outcome_line}")

    print()
    print(_sep("18 . FORENSICS RESULT"))
    if record.exit_price is None:
        print("    Position not yet closed -- forensics will run after exit is confirmed.")
    else:
        print("    (forensics records stored in MemoryStore; see forensics_demo)")

    print()
    print(_sep("19 . MEMORY / LESSON"))
    if memories:
        for i, mem in enumerate(memories, 1):
            print(f"    [{i}] {mem.lesson}")
            print(f"        Condition: {mem.market_condition}")
            print(f"        Confidence: {mem.confidence * 100:.0f}%  Evidence count: {mem.evidence_count}")
    else:
        print("    No memories stored yet for this trade.")
    if memory_reasoning:
        print()
        print("    AI memory influence on this decision:")
        print(_wrap(memory_reasoning))

    print()
    print(W)
    print("  DECISION TRACE SUMMARY")
    print(W)
    print()
    print("  ? Why did TradePilot make this trade?")
    print(_wrap(thesis_text or summary_text or "(see thesis above)"))
    print()
    print("  ? Why was it allowed?")
    print(f"    RiskEngine: {record.risk_decision.value}. Demo cap ${demo_cap:.2f} enforced.")
    print()
    print("  ? Who approved execution?")
    print("    Human operator -- explicit [y/N] confirmation required.")
    print()
    print("  ? What actually happened at Alpaca?")
    status_disp  = live_status or record.execution_status.value
    filled_disp  = live_filled_qty if live_filled_qty is not None else record.filled_quantity
    fill_pr_disp = live_fill_price if live_fill_price is not None else record.average_fill_price
    fill_pr_str  = f"${float(fill_pr_disp):.2f}" if fill_pr_disp is not None else "pending"
    print(f"    Order {alpaca_oid_live or '(id unknown)'} ->")
    print(f"    Status: {status_disp} | Filled: {filled_disp} shares @ {fill_pr_str}")
    print()
    print("  ? What did the system learn?")
    if memories:
        print(_wrap(memories[0].lesson))
    else:
        print("    No memories yet -- run forensics after position closes.")
    print()
    print(_sep("20 . AI DECISION EVIDENCE AUDIT"))
    evidence_audit = data.get("evidence_audit")
    if evidence_audit:
        print(f"    Overall Status : {evidence_audit.overall_status.value}")
        print(f"    Summary        : {evidence_audit.summary}")
        if evidence_audit.claims:
            print("    Audited Claims :")
            for c in evidence_audit.claims:
                src = f" ({c.evidence_source})" if c.evidence_source else ""
                print(f"      * [{c.status.value}] ({c.severity.value}) {c.claim}{src}")
                print(f"        {c.explanation}")
    else:
        print("    (no audit recorded)")
    print()
    print("  ? Was the decision evidence verified?")
    if evidence_audit:
        print(f"    Auditor: {evidence_audit.overall_status.value} ({evidence_audit.supported_count} supported, {evidence_audit.unsupported_count} unsupported, {evidence_audit.unverifiable_count} unverifiable)")
    else:
        print("    Auditor: Evidence audit not conducted.")
    print()
    print(W)
    print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "TradePilot Explainable Decision Trace (read-only). "
            "Answers why a trade was made, allowed, and what happened."
        )
    )
    parser.add_argument(
        "order_id",
        type=str,
        help="Alpaca order UUID OR TradePilot trade/client order UUID",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/tradepilot.db",
        help="Path to the TradePilot SQLite journal (default: data/tradepilot.db)",
    )
    args = parser.parse_args()

    journal = TradeJournal(db_path=args.db)

    # Pass the raw identifier; build_trace tries all lookup strategies internally.
    exit_code = build_trace(journal, alpaca_order_id=args.order_id)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
