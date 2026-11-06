import os
from typing import Any, Dict, Optional, Union
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from datetime import datetime, date
import uuid
from pydantic import BaseModel, ValidationError, Field, field_validator
import dataclasses

from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore
from tradepilot.decision_trace import get_trace_data
from tradepilot.alpaca.market_data import fetch_market_snapshot
from tradepilot.alpaca.portfolio import fetch_portfolio_state
from tradepilot.agents.llm_strategy import LLMStrategyAgent
from tradepilot.agents.evidence_auditor import EvidenceAuditor
from tradepilot.core.config import settings
from tradepilot.core.models import (
    Action, OrderType, TimeInForce, TradeProposal,
    RiskStatus, StrategyProposal, PendingProposal, TradeLifecycleStatus,
    ApprovedTrade, TradeRecord, ExecutionStatus, MarketSnapshot, PortfolioState,
    EvidenceAuditReport
)
from tradepilot.core.risk_engine import RiskEngine
from tradepilot.core.sizing import calculate_position_size
from tradepilot.alpaca.execution import AlpacaPaperExecutor
from tradepilot.backtest.data import fetch_historical_bars
from tradepilot.backtest.engine import BacktestEngine

class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Stock symbol to backtest")
    start_date: date = Field(..., description="Start date")
    end_date: date = Field(..., description="End date")
    initial_capital: float = Field(..., gt=0, description="Initial capital in USD")

    @field_validator("end_date")
    def end_date_after_start_date(cls, v, info):
        if 'start_date' in info.data:
            if v <= info.data['start_date']:
                raise ValueError("end_date must be strictly after start_date")
            if (v - info.data['start_date']).days > 365 * 5:
                raise ValueError("maximum backtest range is 5 years")
        return v
    
    @field_validator("symbol")
    def uppercase_symbol(cls, v):
        if not v or not v.strip():
            raise ValueError("symbol cannot be empty")
        return v.upper().strip()

class RiskEvaluationRequest(BaseModel):
    snapshot_timestamp: datetime
    strategy_proposal: StrategyProposal

class AuditRequest(BaseModel):
    proposal: Union[StrategyProposal, TradeProposal, Dict[str, Any]]
    snapshot: Optional[MarketSnapshot] = None
    portfolio: Optional[PortfolioState] = None
    symbol: Optional[str] = None

app = FastAPI(title="TradePilot UI")

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
DB_PATH = "data/tradepilot.db"

@app.get("/api/trace/{trade_id}")
def get_trace(trade_id: str) -> Dict[str, Any]:
    journal = TradeJournal(db_path=DB_PATH)
    data = get_trace_data(journal, trade_id_str=trade_id)
    if data is None:
        # Fallback if they pass alpaca_order_id
        data = get_trace_data(journal, alpaca_order_id=trade_id)
    
    if data is None:
        raise HTTPException(status_code=404, detail="TradeRecord not found")

    # Convert Pydantic models to dicts for JSON serialization
    return {
        "record": data["record"].model_dump(),
        "live_status": data["live_status"],
        "live_filled_qty": data["live_filled_qty"],
        "live_fill_price": data["live_fill_price"],
        "live_submitted": data["live_submitted"],
        "alpaca_oid_live": data["alpaca_oid_live"],
        "client_oid_live": data["client_oid_live"],
        "memories": [m.model_dump() for m in data["memories"]],
        "thesis_text": data["thesis_text"],
        "evidence_text": data["evidence_text"],
        "summary_text": data["summary_text"],
        "memory_reasoning": data["memory_reasoning"],
        "demo_cap": data["demo_cap"],
        "snapshot_price": data["snapshot_price"],
        "actual_fill_price": data["actual_fill_price"],
        "demo_note": data["demo_note"],
        "outcome_line": data["outcome_line"],
        "evidence_audit": (
            data["evidence_audit"].model_dump()
            if hasattr(data.get("evidence_audit"), "model_dump")
            else data.get("evidence_audit")
        ),
        "system_status": {
            "paper_trading": settings.alpaca_paper_trade,
            "demo_cap": settings.max_demo_order_notional
        }
    }


@app.get("/api/market/{symbol}", summary="Latest market data snapshot for a symbol")
def get_market(symbol: str) -> Dict[str, Any]:
    """
    Return the latest available daily-bar market data for *symbol*.

    Data is sourced from Alpaca historical bars (IEX feed, daily resolution).
    This is **not** real-time streaming data — it reflects the most recent
    completed trading day available from Alpaca.

    Raises:
        400 — symbol is unknown, or fewer than 50 days of history are available.
        503 — unable to reach the Alpaca data API.
    """
    symbol = symbol.upper().strip()
    try:
        snapshot = fetch_market_snapshot(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return snapshot.model_dump()

@app.post("/api/analyze/{symbol}", summary="On-demand AI market analysis")
def analyze_market_endpoint(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper().strip()
    try:
        snapshot = fetch_market_snapshot(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        portfolio = fetch_portfolio_state()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to fetch portfolio state: {exc}")

    try:
        memory_store = MemoryStore(db_path=DB_PATH)
        memories = memory_store.retrieve_memories(symbol)
    except Exception:
        memories = []

    agent = LLMStrategyAgent()
    try:
        strategy_proposal = agent.analyze_market(snapshot, portfolio, memories)
    except RuntimeError:
        # All transient retries exhausted
        raise HTTPException(status_code=503, detail="AI analysis temporarily unavailable.")

    if not strategy_proposal:
        raise HTTPException(status_code=503, detail="AI analysis temporarily unavailable.")

    # Step 10: Run Evidence Audit on generated proposal
    auditor = EvidenceAuditor()
    audit_report = auditor.audit(strategy_proposal, snapshot, portfolio, symbol=symbol)

    return {
        "symbol": symbol,
        "market_snapshot": snapshot.model_dump(),
        "analysis": strategy_proposal.model_dump(),
        "evidence_audit": audit_report.model_dump(),
        "status": "ADVISORY_ONLY"
    }

@app.post("/api/risk/{symbol}", summary="Evaluate Risk deterministically")
def evaluate_risk_endpoint(symbol: str, req: RiskEvaluationRequest) -> Dict[str, Any]:
    symbol = symbol.upper().strip()

    # 1 & 3: Fetch fresh MarketSnapshot (Authoritative)
    try:
        snapshot = fetch_market_snapshot(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Check Staleness
    if snapshot.timestamp != req.snapshot_timestamp:
        raise HTTPException(
            status_code=409,
            detail="Stale analysis: Market data has changed. Please re-analyze first."
        )

    # 4. Fetch PortfolioState (Authoritative)
    try:
        portfolio = fetch_portfolio_state()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to fetch portfolio state: {exc}")

    # Use the validated StrategyProposal from the untrusted frontend
    # Pydantic has already validated its structure, and we've verified market data isn't stale.
    strategy_proposal = req.strategy_proposal

    if strategy_proposal.action == Action.NO_TRADE:
        return {
            "symbol": symbol,
            "action": strategy_proposal.action.value,
            "confidence": strategy_proposal.confidence,
            "thesis": strategy_proposal.thesis,
            "quantity": 0,
            "current_price": snapshot.current_price,
            "stop_loss": strategy_proposal.suggested_stop_loss,
            "risk_status": "NOT_APPLICABLE",
            "risk_reasons": ["AI recommendation does not contain an executable trade proposal."],
            "assumptions": strategy_proposal.assumptions,
            "invalidation_conditions": strategy_proposal.invalidation_conditions,
            "status": "ADVISORY_ONLY",
            "message_risk": "Risk evaluated deterministically.",
            "message_human": "Human approval required before execution."
        }

    # 6. Calculate quantity server-side (Authoritative)
    quantity = calculate_position_size(
        current_price=snapshot.current_price,
        portfolio=portfolio,
        max_pct=settings.max_position_pct
    )

    if quantity <= 0:
        return {
            "symbol": symbol,
            "action": Action.NO_TRADE.value,
            "confidence": strategy_proposal.confidence,
            "thesis": strategy_proposal.thesis,
            "quantity": 0,
            "current_price": snapshot.current_price,
            "stop_loss": strategy_proposal.suggested_stop_loss,
            "risk_status": "NOT_APPLICABLE",
            "risk_reasons": ["Calculated position size is 0 (insufficient funds)."],
            "assumptions": strategy_proposal.assumptions,
            "invalidation_conditions": strategy_proposal.invalidation_conditions,
            "status": "ADVISORY_ONLY",
            "message_risk": "Risk evaluated deterministically.",
            "message_human": "Human approval required before execution."
        }

    try:
        proposal = TradeProposal(
            trade_id=uuid.uuid4(),
            thesis_id=uuid.uuid4(),
            symbol=snapshot.symbol,
            action=strategy_proposal.action,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            quantity=quantity,
            current_price=snapshot.current_price,
            stop_loss=strategy_proposal.suggested_stop_loss,
            confidence=strategy_proposal.confidence,
            reasoning=strategy_proposal.reasoning_summary,
            assumptions=strategy_proposal.assumptions,
            invalidation_conditions=strategy_proposal.invalidation_conditions,
            memory_influence=strategy_proposal.memory_influence
        )
    except ValidationError as e:
        return {
            "symbol": symbol,
            "action": strategy_proposal.action.value,
            "confidence": strategy_proposal.confidence,
            "thesis": strategy_proposal.thesis,
            "quantity": quantity,
            "current_price": snapshot.current_price,
            "stop_loss": strategy_proposal.suggested_stop_loss,
            "risk_status": RiskStatus.REJECTED.value,
            "risk_reasons": [f"TradeProposal validation failed: {e}"],
            "assumptions": strategy_proposal.assumptions,
            "invalidation_conditions": strategy_proposal.invalidation_conditions,
            "status": "ADVISORY_ONLY",
            "message_risk": "Risk evaluated deterministically.",
            "message_human": "Human approval required before execution."
        }

    # 5. Use server-side settings for Risk Engine configuration
    risk_engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit
    )
    decision = risk_engine.evaluate(proposal, portfolio)

    # If APPROVED, persist a PendingProposal in pending_proposals.
    # trade_records is NEVER written to here — it remains execution-only.
    trade_id_str = str(proposal.trade_id)
    audit_report = None
    if decision.status == RiskStatus.APPROVED:
        auditor = EvidenceAuditor()
        audit_report = auditor.audit(proposal, snapshot, portfolio, symbol=symbol)

        pending = PendingProposal(
            trade_id=proposal.trade_id,
            thesis_id=proposal.thesis_id,
            symbol=proposal.symbol,
            action=proposal.action,
            quantity=proposal.quantity,
            reference_price=proposal.current_price,
            stop_loss=proposal.stop_loss,
            snapshot_timestamp=snapshot.timestamp,
            risk_decision=decision.status,
            risk_reasons=decision.reasons,
            lifecycle_status=TradeLifecycleStatus.RISK_APPROVED,
            proposal_snapshot=proposal,
            evidence_audit=audit_report,
        )
        journal = TradeJournal(db_path=DB_PATH)
        journal.save_pending_proposal(pending)
        journal.save_evidence_audit(audit_report, trade_id=proposal.trade_id)

    return {
        "symbol": symbol,
        "action": strategy_proposal.action.value,
        "confidence": strategy_proposal.confidence,
        "thesis": strategy_proposal.thesis,
        "quantity": quantity,
        "current_price": snapshot.current_price,
        "stop_loss": strategy_proposal.suggested_stop_loss,
        "risk_status": decision.status.value,
        "risk_reasons": decision.reasons,
        "trade_id": trade_id_str if decision.status == RiskStatus.APPROVED else None,
        "evidence_audit": audit_report.model_dump() if audit_report else None,
        "assumptions": strategy_proposal.assumptions,
        "invalidation_conditions": strategy_proposal.invalidation_conditions,
        "status": "ADVISORY_ONLY",
        "message_risk": "Risk evaluated deterministically.",
        "message_human": "Human approval required before execution."
    }

@app.post("/api/approve/{trade_id}", summary="Human approval of a risk-approved proposal")
def approve_trade_endpoint(trade_id: str) -> Dict[str, Any]:
    """Explicit human approval of a RISK_APPROVED PendingProposal.

    This endpoint:
      - Looks up the PendingProposal in pending_proposals by trade_id.
      - Re-validates that market and portfolio conditions still permit the
        EXACT stored proposal (read-only — proposal terms are never mutated).
      - Atomically transitions lifecycle_status from RISK_APPROVED to
        HUMAN_APPROVED using a conditional SQL UPDATE.

    It does NOT:
      - Submit an Alpaca order.
      - Call execute().
      - Modify trade_records.
      - Create a TradeRecord.
      - Modify any position.
    """
    try:
        tid = uuid.UUID(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade_id format.")

    journal = TradeJournal(db_path=DB_PATH)
    pending = journal.get_pending_proposal(tid)

    if pending is None:
        raise HTTPException(status_code=404, detail="Pending proposal not found.")

    if pending.lifecycle_status != TradeLifecycleStatus.RISK_APPROVED:
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is already in state '{pending.lifecycle_status.value}'. Cannot approve again."
        )

    # Re-validate: fetch authoritative current state — READ ONLY against stored proposal.
    try:
        fresh_snapshot = fetch_market_snapshot(pending.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Staleness check — stored snapshot_timestamp vs authoritative current snapshot.
    if fresh_snapshot.timestamp != pending.snapshot_timestamp:
        raise HTTPException(
            status_code=409,
            detail=(
                "Market data has changed since risk evaluation. "
                "Please re-analyze and re-evaluate risk before approving."
            )
        )

    try:
        fresh_portfolio = fetch_portfolio_state()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to fetch portfolio state: {exc}")

    # Re-run the EXACT stored TradeProposal through RiskEngine with current portfolio.
    # The stored proposal is not modified — only used as input to verify still valid.
    risk_engine = RiskEngine(
        max_position_pct=settings.max_position_pct,
        max_risk_usd=settings.max_risk_usd,
        max_open_positions=settings.max_open_positions,
        daily_loss_limit=settings.daily_loss_limit
    )
    revalidation = risk_engine.evaluate(pending.proposal_snapshot, fresh_portfolio)
    if revalidation.status != RiskStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail=(
                "Current market or portfolio conditions no longer satisfy the risk rules "
                "for this proposal. Please re-analyze and re-evaluate risk.\n"
                f"Reasons: {'; '.join(revalidation.reasons)}"
            )
        )

    # Atomic conditional transition: RISK_APPROVED → HUMAN_APPROVED.
    # Returns False if another request already transitioned it.
    from datetime import timezone as _tz
    approved_at = datetime.now(_tz.utc)
    success = journal.approve_pending_proposal(tid, approved_at)
    if not success:
        raise HTTPException(
            status_code=409,
            detail="Proposal was already approved by a concurrent request."
        )

    return {
        "trade_id": str(pending.trade_id),
        "symbol": pending.symbol,
        "action": pending.action.value,
        "quantity": pending.quantity,
        "reference_price": pending.reference_price,
        "stop_loss": pending.stop_loss,
        "lifecycle_status": TradeLifecycleStatus.HUMAN_APPROVED.value,
        "human_approved_at": approved_at.isoformat(),
        "message": "Human approval recorded. Awaiting Step 5 execution."
    }


@app.post("/api/execute/{trade_id}", summary="Execute a human-approved paper trade")
def execute_trade_endpoint(trade_id: str) -> Dict[str, Any]:
    """Execute a HUMAN_APPROVED PendingProposal against Alpaca Paper Trading.

    Safety invariants enforced:
      ─ Only HUMAN_APPROVED proposals may be executed.
      ─ The stored trade terms (symbol, action, quantity, order_type) are
        used verbatim — the request body carries NO trade parameters.
      ─ HUMAN_APPROVED → EXECUTION_PENDING is an atomic conditional UPDATE
        (rowcount == 1); any concurrent duplicate request gets a 409.
      ─ If the proposal is already EXECUTION_PENDING (crash-recovery path),
        the existing Alpaca order is looked up by client_order_id (== trade_id)
        before any new submission is attempted.
      ─ If already EXECUTED, returns an idempotent 200 with the existing record.
      ─ No LLM calls. No re-analysis. No re-sizing. No trade term mutation.
      ─ paper=True and alpaca_paper_trade=True are enforced by AlpacaPaperExecutor.

    Creates a TradeRecord in trade_records.  Does NOT overwrite existing records
    (historical PLTR/FILLED record is preserved by ON CONFLICT DO UPDATE in
    journal.save_record which only overwrites the `data` column, never deletes).
    """
    from datetime import timezone as _tz

    try:
        tid = uuid.UUID(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade_id format.")

    journal = TradeJournal(db_path=DB_PATH)
    pending = journal.get_pending_proposal(tid)

    if pending is None:
        raise HTTPException(status_code=404, detail="Pending proposal not found.")

    # ── Idempotency: already EXECUTED ──────────────────────────────────────
    if pending.lifecycle_status == TradeLifecycleStatus.EXECUTED:
        existing = journal.get_record(tid)
        if existing:
            return {
                "trade_id": str(existing.trade_id),
                "symbol": existing.symbol,
                "action": existing.action.value,
                "quantity": existing.quantity,
                "alpaca_order_id": existing.alpaca_order_id,
                "execution_status": existing.execution_status.value,
                "filled_quantity": existing.filled_quantity,
                "average_fill_price": existing.average_fill_price,
                "lifecycle_status": TradeLifecycleStatus.EXECUTED.value,
                "message": "Trade already executed. Returning existing record.",
                "idempotent": True,
            }
        # Record missing but status is EXECUTED — data inconsistency, surface as error
        raise HTTPException(
            status_code=409,
            detail="Proposal is EXECUTED but no TradeRecord found. Data inconsistency."
        )

    # ── Crash-recovery: already EXECUTION_PENDING ─────────────────────────
    # Application may have crashed after the Alpaca submission but before
    # persisting the TradeRecord.  Reconcile via client_order_id (== trade_id)
    # before attempting another submission.
    if pending.lifecycle_status == TradeLifecycleStatus.EXECUTION_PENDING:
        existing_record = journal.get_record(tid)
        if existing_record:
            # TradeRecord exists — a previous request fully completed.
            # Idempotent response.
            return {
                "trade_id": str(existing_record.trade_id),
                "symbol": existing_record.symbol,
                "action": existing_record.action.value,
                "quantity": existing_record.quantity,
                "alpaca_order_id": existing_record.alpaca_order_id,
                "execution_status": existing_record.execution_status.value,
                "filled_quantity": existing_record.filled_quantity,
                "average_fill_price": existing_record.average_fill_price,
                "lifecycle_status": TradeLifecycleStatus.EXECUTION_PENDING.value,
                "message": "Execution already in progress. Returning existing record.",
                "idempotent": True,
            }
        # No TradeRecord yet.  Attempt Alpaca reconciliation via the stable
        # client_order_id before deciding whether to submit a new order.
        # (Reconciliation is best-effort; if Alpaca lookup fails we surface
        #  a conflict rather than risk a duplicate submission.)
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.common.exceptions import APIError
            if settings.alpaca_api_key and settings.alpaca_secret_key:
                reconcile_client = TradingClient(
                    api_key=settings.alpaca_api_key,
                    secret_key=settings.alpaca_secret_key,
                    paper=True,
                )
                try:
                    order = reconcile_client.get_order_by_client_id(str(tid))
                    # Alpaca already has this order.  Build a TradeRecord from it
                    # and persist, then mark EXECUTED, rather than submit again.
                    status_map = {
                        "accepted":        ExecutionStatus.ACCEPTED,
                        "pending_new":     ExecutionStatus.PENDING,
                        "new":             ExecutionStatus.ACCEPTED,
                        "partially_filled": ExecutionStatus.PARTIALLY_FILLED,
                        "filled":          ExecutionStatus.FILLED,
                        "rejected":        ExecutionStatus.REJECTED,
                        "canceled":        ExecutionStatus.CANCELLED,
                    }
                    raw_status = order.status.value if hasattr(order.status, 'value') else str(order.status)
                    exec_status = status_map.get(raw_status, ExecutionStatus.PENDING)
                    proposal = pending.proposal_snapshot
                    record = TradeRecord(
                        trade_id=pending.trade_id,
                        thesis_id=pending.thesis_id,
                        alpaca_order_id=str(order.id),
                        symbol=proposal.symbol,
                        action=proposal.action,
                        quantity=proposal.quantity,
                        filled_quantity=float(order.filled_qty) if order.filled_qty else 0.0,
                        average_fill_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                        original_thesis=proposal.reasoning,
                        original_confidence=proposal.confidence,
                        assumptions=proposal.assumptions,
                        invalidation_conditions=proposal.invalidation_conditions,
                        risk_decision=RiskStatus.APPROVED,
                        execution_status=exec_status,
                        entry_price=float(order.filled_avg_price) if (order.filled_avg_price and exec_status == ExecutionStatus.FILLED) else None,
                    )
                    journal.save_record(record)
                    journal.mark_proposal_executed(tid)
                    return {
                        "trade_id": str(record.trade_id),
                        "symbol": record.symbol,
                        "action": record.action.value,
                        "quantity": record.quantity,
                        "alpaca_order_id": record.alpaca_order_id,
                        "execution_status": record.execution_status.value,
                        "filled_quantity": record.filled_quantity,
                        "average_fill_price": record.average_fill_price,
                        "lifecycle_status": TradeLifecycleStatus.EXECUTED.value,
                        "message": "Reconciled with existing Alpaca order (crash-recovery).",
                        "recovered": True,
                    }
                except APIError:
                    # No matching order found on Alpaca — safe to submit.
                    pass
        except Exception:
            # Alpaca client unavailable; surface conflict to prevent blind re-submit.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Proposal is in EXECUTION_PENDING state from a previous request. "
                    "Cannot reconcile with Alpaca. Investigate before retrying."
                )
            )
        # Fall through: Alpaca has no record of this order — proceed with submission
        # below (do NOT re-run the atomic transition; status is already PENDING).
        # This covers the rare case where the app crashed between the DB commit
        # and the Alpaca HTTP call.
        _already_pending = True
    else:
        _already_pending = False

    # ── Lifecycle gate: must be HUMAN_APPROVED ─────────────────────────────
    if not _already_pending and pending.lifecycle_status != TradeLifecycleStatus.HUMAN_APPROVED:
        status_val = pending.lifecycle_status.value
        if pending.lifecycle_status == TradeLifecycleStatus.RISK_APPROVED:
            detail = (
                f"Cannot execute: proposal is '{status_val}'. "
                "Human approval is required before execution."
            )
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(
            status_code=409,
            detail=f"Cannot execute: proposal is in unexpected state '{status_val}'."
        )

    # ── Atomic HUMAN_APPROVED → EXECUTION_PENDING ─────────────────────────
    if not _already_pending:
        transitioned = journal.transition_to_execution_pending(tid)
        if not transitioned:
            # Another concurrent request won the race — return conflict
            raise HTTPException(
                status_code=409,
                detail=(
                    "Proposal was already claimed for execution by a concurrent request. "
                    "Retry in a moment to get the idempotent result."
                )
            )

    # ── Build ApprovedTrade from the EXACT persisted proposal ──────────────
    # proposal_snapshot is the verbatim TradeProposal stored at risk-evaluation
    # time.  No mutation of quantity, action, symbol, or price is allowed.
    proposal = pending.proposal_snapshot
    approved_trade = ApprovedTrade(
        trade_id=pending.trade_id,
        proposal=proposal,
        approved_at=pending.human_approved_at or datetime.now(_tz.utc),
    )

    # ── Execute via AlpacaPaperExecutor ─────────────────────────────────
    try:
        executor = AlpacaPaperExecutor()
    except ValueError as e:
        # paper=False safety violation — should never happen in production config
        raise HTTPException(status_code=500, detail=f"Executor safety violation: {e}")

    result = executor.execute(approved_trade)  # returns ExecutionResult, never raises

    # ── Persist TradeRecord ───────────────────────────────────────────
    record = TradeRecord(
        trade_id=result.trade_id,
        thesis_id=result.thesis_id,
        alpaca_order_id=result.alpaca_order_id,
        symbol=result.symbol,
        action=result.action,
        quantity=result.quantity,
        filled_quantity=result.filled_quantity,
        average_fill_price=result.average_fill_price,
        original_thesis=proposal.reasoning,
        original_confidence=proposal.confidence,
        assumptions=proposal.assumptions,
        invalidation_conditions=proposal.invalidation_conditions,
        risk_decision=RiskStatus.APPROVED,
        execution_status=result.status,
        entry_price=result.average_fill_price if result.status == ExecutionStatus.FILLED else None,
    )
    journal.save_record(record)

    # ── EXECUTION_PENDING → EXECUTED ────────────────────────────────────
    journal.mark_proposal_executed(tid)

    return {
        "trade_id": str(result.trade_id),
        "thesis_id": str(result.thesis_id),
        "symbol": result.symbol,
        "action": result.action.value,
        "quantity": result.quantity,
        "order_type": result.order_type.value,
        "client_order_id": str(result.client_order_id),
        "alpaca_order_id": result.alpaca_order_id,
        "execution_status": result.status.value,
        "filled_quantity": result.filled_quantity,
        "average_fill_price": result.average_fill_price,
        "lifecycle_status": TradeLifecycleStatus.EXECUTED.value,
        "message": result.message,
        "submitted_timestamp": result.submitted_timestamp.isoformat(),
    }

@app.get("/api/portfolio")
def get_portfolio() -> Dict[str, Any]:
    try:
        portfolio = fetch_portfolio_state()
        return portfolio.model_dump()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/api/stats")
def get_stats() -> Dict[str, Any]:
    journal = TradeJournal(db_path=DB_PATH)
    pending = journal.list_all_pending_proposals()
    records = journal.list_all_records()
    
    generated_count = "Limitation: Risk-rejected proposals are not persisted. Count reflects only approved proposals."
    passed_risk = len(pending)
    human_approved = sum(1 for p in pending if p.lifecycle_status in (
        TradeLifecycleStatus.HUMAN_APPROVED, TradeLifecycleStatus.EXECUTION_PENDING, TradeLifecycleStatus.EXECUTED))
    submitted = len(records)
    
    execution_statuses = {}
    for r in records:
        st = r.execution_status.value
        execution_statuses[st] = execution_statuses.get(st, 0) + 1
        
    return {
        "proposals_generated": generated_count,
        "passed_risk": passed_risk,
        "human_approved": human_approved,
        "submitted_to_alpaca": submitted,
        "execution_statuses": execution_statuses
    }

@app.get("/api/trades")
def get_trades(symbol: str = None, action: str = None, status: str = None) -> Dict[str, Any]:
    journal = TradeJournal(db_path=DB_PATH)
    pending = journal.list_all_pending_proposals()
    records = journal.list_all_records()
    
    record_map = {str(r.trade_id): r for r in records}
    unified_trades = []
    processed_ids = set()
    
    for p in pending:
        tid_str = str(p.trade_id)
        processed_ids.add(tid_str)
        rec = record_map.get(tid_str)
        
        trade_summary = {
            "trade_id": tid_str,
            "created_at": p.created_at.isoformat(),
            "symbol": p.symbol,
            "action": p.action.value,
            "quantity": p.quantity,
            "order_type": p.proposal_snapshot.order_type.value,
            "lifecycle_status": p.lifecycle_status.value,
            "execution_status": rec.execution_status.value if rec else None,
            "filled_quantity": rec.filled_quantity if rec else 0.0,
            "average_fill_price": rec.average_fill_price if rec else None,
            "alpaca_order_id": rec.alpaca_order_id if rec else None,
        }
        unified_trades.append(trade_summary)
        
    for r in records:
        tid_str = str(r.trade_id)
        if tid_str not in processed_ids:
            trade_summary = {
                "trade_id": tid_str,
                "created_at": r.entry_timestamp.isoformat() if r.entry_timestamp else "",
                "symbol": r.symbol,
                "action": r.action.value,
                "quantity": r.quantity,
                "order_type": "UNKNOWN",
                "lifecycle_status": "EXECUTED",
                "execution_status": r.execution_status.value,
                "filled_quantity": r.filled_quantity,
                "average_fill_price": r.average_fill_price,
                "alpaca_order_id": r.alpaca_order_id,
            }
            unified_trades.append(trade_summary)
            
    if symbol:
        unified_trades = [t for t in unified_trades if t["symbol"].upper() == symbol.upper()]
    if action:
        unified_trades = [t for t in unified_trades if t["action"].upper() == action.upper()]
    if status:
        unified_trades = [t for t in unified_trades if 
                          (t["execution_status"] and t["execution_status"].upper() == status.upper()) or 
                          (t["lifecycle_status"].upper() == status.upper())]
                          
    unified_trades.sort(key=lambda x: x["created_at"], reverse=True)
    return {"trades": unified_trades}

@app.get("/api/trades/{trade_id}")
def get_trade(trade_id: str) -> Dict[str, Any]:
    journal = TradeJournal(db_path=DB_PATH)
    try:
        tid = uuid.UUID(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade_id")
        
    pending = journal.get_pending_proposal(tid)
    record = journal.get_record(tid)
    
    if not pending and not record:
        raise HTTPException(status_code=404, detail="Trade not found")
        
    details = {
        "trade_id": trade_id,
        "has_decision_trace": True
    }
    
    if pending:
        details["thesis_id"] = str(pending.thesis_id)
        details["symbol"] = pending.symbol
        details["action"] = pending.action.value
        details["quantity"] = pending.quantity
        details["order_type"] = pending.proposal_snapshot.order_type.value
        details["time_in_force"] = pending.proposal_snapshot.time_in_force.value
        details["lifecycle_status"] = pending.lifecycle_status.value
    elif record:
        details["thesis_id"] = str(record.thesis_id)
        details["symbol"] = record.symbol
        details["action"] = record.action.value
        details["quantity"] = record.quantity
        details["order_type"] = "UNKNOWN"
        details["time_in_force"] = "UNKNOWN"
        details["lifecycle_status"] = "EXECUTED"
        
    if record:
        details["execution_status"] = record.execution_status.value
        details["filled_quantity"] = record.filled_quantity
        details["average_fill_price"] = record.average_fill_price
        details["client_order_id"] = str(record.trade_id)
        details["alpaca_order_id"] = record.alpaca_order_id
    else:
        details["execution_status"] = None
        details["filled_quantity"] = 0.0
        details["average_fill_price"] = None
        details["client_order_id"] = None
        details["alpaca_order_id"] = None

    # Step 10: Include Evidence Audit in trade details if available
    audit = journal.get_evidence_audit_by_trade_id(tid)
    details["evidence_audit"] = audit.model_dump() if audit else None

    return details

@app.post("/api/audit", summary="Advisory AI Decision Evidence Audit")
def audit_endpoint(req: AuditRequest) -> Dict[str, Any]:
    """
    Advisory evidence audit endpoint.
    Deterministically evaluates proposal claims against market snapshot and portfolio state.
    Produces zero orders, makes no journal mutations, and cannot execute or approve trades.
    """
    auditor = EvidenceAuditor()
    report = auditor.audit(req.proposal, req.snapshot, req.portfolio, symbol=req.symbol)
    return report.model_dump()

# Ensure web directory exists for mounting
os.makedirs(WEB_DIR, exist_ok=True)

# Mount static files at /
@app.get("/")
def read_index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))

@app.post("/api/backtest", summary="Run a historical backtest")
def run_backtest_endpoint(req: BacktestRequest) -> Dict[str, Any]:
    try:
        df = fetch_historical_bars(req.symbol, req.start_date, req.end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch historical data: {exc}")

    engine = BacktestEngine(
        initial_capital=req.initial_capital,
        risk_params={
            "max_position_pct": settings.max_position_pct,
            "max_risk_usd": settings.max_risk_usd,
            "max_open_positions": settings.max_open_positions,
            "daily_loss_limit": settings.daily_loss_limit,
        },
        max_pct=settings.max_position_pct,
    )
    
    try:
        result = engine.run(req.symbol, df, req.start_date, req.end_date)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest engine error: {exc}")
        
    return dataclasses.asdict(result)

app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
