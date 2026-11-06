"""
tests/test_evidence_auditor.py

Comprehensive tests for Step 10: AI Decision Evidence Auditor.
Verifies all requirements:
  - Supported and unsupported numeric claims.
  - Missing and incomplete evidence (absent fields not treated as false).
  - Unverifiable claims (forward-looking assumptions, macro claims).
  - Incorrect timestamps or mismatched snapshots.
  - NO_TRADE proposals (without inventing trade evidence).
  - Empty claim sets and audit failures (honest reporting, not falsely passed).
  - Preservation of original decision data (immutability).
  - API / UI serialization and endpoints.
  - Safety invariants: No Alpaca orders, no Gemini calls during deterministic audits,
    and no mutations to trade_records or pending_proposals.
"""

from __future__ import annotations

import io
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradepilot.agents.evidence_auditor import EvidenceAuditor
from tradepilot.api import app
from tradepilot.core.models import (
    Action,
    AuditStatus,
    ClaimSeverity,
    ClaimStatus,
    EvidenceAuditClaim,
    EvidenceAuditReport,
    ExecutionStatus,
    MarketSnapshot,
    PendingProposal,
    PortfolioState,
    RiskStatus,
    StrategyProposal,
    TradeLifecycleStatus,
    TradeProposal,
    TradeRecord,
)
from tradepilot.decision_trace import build_trace, get_trace_data
from tradepilot.memory.journal import TradeJournal


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="PLTR",
        current_price=186.24,
        timestamp=datetime(2026, 8, 31, 13, 0, 0, tzinfo=timezone.utc),
        volume=50_000_000.0,
        previous_close=185.94,
        price_change_pct=0.0016,  # 0.16%
        sma_20=171.03,
        sma_50=143.74,
        volatility=0.021,
    )


@pytest.fixture
def sample_portfolio() -> PortfolioState:
    return PortfolioState(
        account_equity=50000.0,
        buying_power=40000.0,
        cash=10000.0,
        current_daily_loss=0.0,
        open_positions=["PLTR"],
        number_of_open_positions=1,
        position_quantities={"PLTR": 10.0},
    )


@pytest.fixture
def supported_proposal() -> StrategyProposal:
    return StrategyProposal(
        action=Action.BUY,
        confidence=0.85,
        thesis="PLTR is in a strong uptrend with price (186.24) trading above both SMA20 (171.03) and SMA50 (143.74).",
        evidence="Price: 186.24, SMA20: 171.03, SMA50: 143.74, Momentum: 0.16%, Volatility: 0.021, Volume: 50M, Cash: 10000",
        assumptions=[
            "Upward price momentum will persist.",
            "Price will respect SMA20 as dynamic support.",
        ],
        invalidation_conditions=[
            "Price drops below SMA20 at 171.03.",
            "Momentum turns significantly negative.",
        ],
        suggested_stop_loss=170.0,
        reasoning_summary="Bullish alignment across moving averages justifies long entry.",
    )


@pytest.fixture
def tmp_journal(tmp_path) -> TradeJournal:
    return TradeJournal(db_path=str(tmp_path / "auditor_test.db"))


# ── 1. Supported Numeric Claims ───────────────────────────────────────────────

def test_audit_all_supported_claims(sample_snapshot, sample_portfolio, supported_proposal):
    """Audit verifies all matching numeric metrics and marks overall status PASSED."""
    auditor = EvidenceAuditor()
    report = auditor.audit(
        supported_proposal,
        snapshot=sample_snapshot,
        portfolio=sample_portfolio,
        symbol="PLTR",
    )

    assert report.overall_status == AuditStatus.PASSED
    assert report.evidence_available is True
    assert report.supported_count >= 5  # Price, SMA20, SMA50, Momentum, Volatility, etc.
    assert report.unsupported_count == 0
    assert report.unverifiable_count >= 2  # Forward-looking assumptions

    # Check individual supported claims
    price_claims = [c for c in report.claims if c.evidence_source == "MarketSnapshot.current_price"]
    assert len(price_claims) >= 1
    assert price_claims[0].status == ClaimStatus.SUPPORTED
    assert price_claims[0].claimed_value == "186.24"
    assert price_claims[0].observed_value == "186.24"

    sma20_claims = [c for c in report.claims if "sma_20" in (c.evidence_source or "")]
    assert len(sma20_claims) >= 1
    assert sma20_claims[0].status == ClaimStatus.SUPPORTED

    sma50_claims = [c for c in report.claims if "sma_50" in (c.evidence_source or "")]
    assert len(sma50_claims) >= 1
    assert sma50_claims[0].status == ClaimStatus.SUPPORTED


# ── 2. Unsupported Numeric Claims ─────────────────────────────────────────────

def test_audit_unsupported_price_claim(sample_snapshot, sample_portfolio):
    """Contradicted price claim is classified as UNSUPPORTED with HIGH severity."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="PLTR price is 210.00 above resistance.",
        evidence="Price: 210.00, SMA20: 171.03, SMA50: 143.74",
        assumptions=["Momentum persists."],
        invalidation_conditions=["Price drops."],
        suggested_stop_loss=170.0,
        reasoning_summary="Breakout entry.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.overall_status == AuditStatus.FAILED
    assert report.unsupported_count >= 1

    unsupported = [c for c in report.claims if c.status == ClaimStatus.UNSUPPORTED]
    price_claim = next(c for c in unsupported if "Price" in c.claim or "current_price" in (c.evidence_source or ""))
    assert price_claim.status == ClaimStatus.UNSUPPORTED
    assert price_claim.severity in (ClaimSeverity.HIGH, ClaimSeverity.CRITICAL)
    assert "210" in price_claim.claimed_value
    assert "186.24" in price_claim.observed_value
    assert "contradicts" in price_claim.explanation.lower() or "discrepancy" in price_claim.explanation.lower()


def test_audit_unsupported_relation_claim(sample_snapshot, sample_portfolio):
    """Claim that price is below SMA20 when price is actually above SMA20 is UNSUPPORTED."""
    proposal = StrategyProposal(
        action=Action.SELL,
        confidence=0.7,
        thesis="Price trading below SMA20 indicates bearish breakdown.",
        evidence="Price: 186.24, SMA20: 171.03",
        assumptions=["Sell-off continues."],
        invalidation_conditions=["Recovers SMA20."],
        suggested_stop_loss=190.0,
        reasoning_summary="Exit position.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.overall_status == AuditStatus.FAILED
    rel_claim = next(c for c in report.claims if c.claim == "Price is trading below SMA20")
    assert rel_claim.status == ClaimStatus.UNSUPPORTED
    assert rel_claim.severity == ClaimSeverity.HIGH


def test_audit_unsupported_momentum_claim(sample_snapshot, sample_portfolio):
    """Claiming +5.0% momentum when actual price_change_pct is 0.16% is UNSUPPORTED."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.75,
        thesis="Massive surge in momentum.",
        evidence="Price: 186.24, Momentum: +5.00%",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="High momentum play.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.overall_status == AuditStatus.FAILED
    mom_claim = next(c for c in report.claims if "Momentum" in c.claim)
    assert mom_claim.status == ClaimStatus.UNSUPPORTED


# ── 3. Missing and Incomplete Evidence ────────────────────────────────────────

def test_audit_missing_market_snapshot(sample_portfolio, supported_proposal):
    """When snapshot is None, overall_status is MISSING_EVIDENCE and claims are UNVERIFIABLE."""
    auditor = EvidenceAuditor()
    report = auditor.audit(
        supported_proposal,
        snapshot=None,
        portfolio=sample_portfolio,
        symbol="PLTR",
    )

    assert report.overall_status == AuditStatus.MISSING_EVIDENCE
    assert report.evidence_available is False
    assert report.supported_count == 0
    assert report.unsupported_count == 0
    assert report.unverifiable_count > 0
    assert all(c.status == ClaimStatus.UNVERIFIABLE for c in report.claims)
    assert "unavailable" in report.summary.lower()


def test_audit_absent_portfolio_not_treated_as_false(sample_snapshot):
    """Absent portfolio does not mark cash/equity claims as UNSUPPORTED; marks UNVERIFIABLE."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Sufficient cash available for entry.",
        evidence="Price: 186.24, Cash: 10000",
        assumptions=["Capital preserved."],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="Normal entry.",
    )

    auditor = EvidenceAuditor()
    # Portfolio is None
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=None, symbol="PLTR")

    cash_claim = next(c for c in report.claims if "cash" in c.claim.lower())
    assert cash_claim.status == ClaimStatus.UNVERIFIABLE
    assert cash_claim.status != ClaimStatus.UNSUPPORTED
    assert "not provided" in cash_claim.explanation.lower()
    # Overall status is INCONCLUSIVE due to evidence gap on claimed portfolio metric (Test-versus-semantics rule)
    assert report.overall_status == AuditStatus.INCONCLUSIVE


# ── 4. Unverifiable Claims (Forward-Looking Assumptions) ──────────────────────

def test_audit_forward_looking_assumptions_unverifiable(sample_snapshot, sample_portfolio):
    """Qualitative and future predictions are classified as UNVERIFIABLE, not false."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Price is 186.24.",
        evidence="Price: 186.24",
        assumptions=[
            "Earnings report next Thursday will beat consensus.",
            "Upward price momentum will persist over coming weeks.",
            "Market macro liquidity will improve.",
        ],
        invalidation_conditions=["Unexpected black swan event."],
        suggested_stop_loss=170.0,
        reasoning_summary="Speculative thesis.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    unverifiable = [c for c in report.claims if c.status == ClaimStatus.UNVERIFIABLE]
    assert len(unverifiable) >= 4  # 3 assumptions + 1 invalidation
    assert any("earnings" in c.claim.lower() for c in unverifiable)
    assert any("liquidity" in c.claim.lower() for c in unverifiable)
    for c in unverifiable:
        assert c.severity in (ClaimSeverity.INFO, ClaimSeverity.LOW)


# ── 5. Mismatched Snapshots and Timestamps ─────────────────────────────────────

def test_audit_symbol_mismatch(sample_snapshot, sample_portfolio, supported_proposal):
    """Mismatched snapshot symbol produces STALE_OR_MISMATCHED and CRITICAL severity."""
    auditor = EvidenceAuditor()
    # Proposal target is AAPL, but snapshot is PLTR
    report = auditor.audit(
        supported_proposal,
        snapshot=sample_snapshot,
        portfolio=sample_portfolio,
        symbol="AAPL",
    )

    assert report.overall_status == AuditStatus.STALE_OR_MISMATCHED
    assert report.unsupported_count == 1
    assert report.claims[0].severity == ClaimSeverity.CRITICAL
    assert "mismatched" in report.summary.lower()


def test_audit_lookahead_timestamp_check(sample_portfolio):
    """Snapshot timestamp far in the future relative to proposal triggers temporal UNSUPPORTED."""
    now = datetime.now(timezone.utc)
    proposal_time = now - timedelta(hours=2)
    # Future snapshot relative to proposal (lookahead bias)
    future_snapshot = MarketSnapshot(
        symbol="PLTR",
        current_price=186.24,
        timestamp=now + timedelta(hours=1),
        volume=50_000_000.0,
        previous_close=185.0,
        price_change_pct=0.005,
        sma_20=171.0,
        sma_50=143.0,
        volatility=0.02,
    )

    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Price is 186.24.",
        evidence="Price: 186.24",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="Lookahead test.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(
        proposal,
        snapshot=future_snapshot,
        portfolio=sample_portfolio,
        symbol="PLTR",
        proposal_timestamp=proposal_time,
    )

    ts_claim = next((c for c in report.claims if "timestamp" in c.claim.lower()), None)
    assert ts_claim is not None
    assert ts_claim.status == ClaimStatus.UNSUPPORTED
    assert ts_claim.severity == ClaimSeverity.HIGH


# ── 6. NO_TRADE Proposals ─────────────────────────────────────────────────────

def test_audit_no_trade_proposal_without_inventing_evidence(sample_snapshot, sample_portfolio):
    """NO_TRADE proposals are audited against facts without inventing trade terms."""
    proposal = StrategyProposal(
        action=Action.NO_TRADE,
        confidence=0.9,
        thesis="Market is consolidating near price (186.24). Evidence does not support high-probability trade.",
        evidence="Price: 186.24, SMA20: 171.03, Volatility: 0.021",
        assumptions=["Consolidation continues."],
        invalidation_conditions=[],
        suggested_stop_loss=None,
        reasoning_summary="Stand aside.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.proposal_action == Action.NO_TRADE
    assert report.overall_status == AuditStatus.PASSED
    assert report.supported_count >= 2  # Price and SMA20
    assert report.unsupported_count == 0
    # Stop loss claim was not invented
    assert not any(c.evidence_source == "MarketSnapshot.current_price vs StopLoss" for c in report.claims)


# ── 7. Empty Claim Sets & Inconclusive Audits ─────────────────────────────────

def test_audit_empty_claim_set_is_inconclusive(sample_snapshot, sample_portfolio):
    """Empty claim sets result in INCONCLUSIVE, not false positive PASSED."""
    empty_proposal = StrategyProposal(
        action=Action.NO_TRADE,
        confidence=0.5,
        thesis="",
        evidence="",
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=None,
        reasoning_summary="",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(empty_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.overall_status == AuditStatus.INCONCLUSIVE
    assert report.supported_count == 0
    assert report.unsupported_count == 0
    assert "inconclusive" in report.summary.lower()


def test_audit_all_unverifiable_claims_is_inconclusive(sample_snapshot, sample_portfolio):
    """When only forward-looking unverifiable claims exist, status is INCONCLUSIVE."""
    qualitative_proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.6,
        thesis="Strong company fundamentals and stellar leadership team.",
        evidence="Management commentary and brand strength.",
        assumptions=["Brand equity drives organic growth."],
        invalidation_conditions=["Executive resignation."],
        suggested_stop_loss=None,
        reasoning_summary="Qualitative investment.",
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(qualitative_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert report.overall_status == AuditStatus.INCONCLUSIVE
    assert report.supported_count == 0
    assert report.unverifiable_count > 0


# ── 8. Preservation of Original Decision Data ─────────────────────────────────

def test_immutability_of_input_proposal(sample_snapshot, sample_portfolio, supported_proposal):
    """Auditor never modifies input proposal, snapshot, or portfolio."""
    orig_thesis = supported_proposal.thesis
    orig_confidence = supported_proposal.confidence
    orig_sl = supported_proposal.suggested_stop_loss
    orig_price = sample_snapshot.current_price
    orig_cash = sample_portfolio.cash

    auditor = EvidenceAuditor()
    report = auditor.audit(supported_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    assert supported_proposal.thesis == orig_thesis
    assert supported_proposal.confidence == orig_confidence
    assert supported_proposal.suggested_stop_loss == orig_sl
    assert sample_snapshot.current_price == orig_price
    assert sample_portfolio.cash == orig_cash


# ── 9. Safety Invariants: No Alpaca Orders & No Side Effects ───────────────────

def test_audit_produces_no_alpaca_orders(sample_snapshot, sample_portfolio, supported_proposal):
    """Auditor must never import or call Alpaca order execution."""
    with patch("tradepilot.alpaca.client.get_alpaca_client") as mock_get_alpaca:
        auditor = EvidenceAuditor()
        auditor.audit(supported_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
        mock_get_alpaca.assert_not_called()


def test_audit_produces_no_gemini_calls_by_default(sample_snapshot, sample_portfolio, supported_proposal):
    """Deterministic audit produces zero LLM calls."""
    auditor = EvidenceAuditor()
    if auditor.client:
        with patch.object(auditor.client.models, "generate_content") as mock_gen:
            auditor.audit(supported_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR", use_llm=False)
            mock_gen.assert_not_called()


def test_journal_isolation(tmp_journal, sample_snapshot, sample_portfolio, supported_proposal):
    """Saving evidence audits writes only to evidence_audits table, never trade_records."""
    auditor = EvidenceAuditor()
    report = auditor.audit(supported_proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")

    trade_id = uuid.uuid4()
    tmp_journal.save_evidence_audit(report, trade_id=trade_id)

    # trade_records and pending_proposals must remain empty
    assert tmp_journal.list_all_records() == []
    assert tmp_journal.list_all_pending_proposals() == []

    # Evidence audit is safely stored
    fetched = tmp_journal.get_evidence_audit_by_trade_id(trade_id)
    assert fetched is not None
    assert fetched.overall_status == AuditStatus.PASSED
    assert fetched.symbol == "PLTR"


# ── 10. API Serialization & Endpoints ─────────────────────────────────────────

def test_api_audit_endpoint(sample_snapshot, sample_portfolio, supported_proposal):
    """POST /api/audit accepts proposal and snapshot, returns EvidenceAuditReport."""
    client = TestClient(app)
    payload = {
        "proposal": supported_proposal.model_dump(),
        "snapshot": sample_snapshot.model_dump(),
        "portfolio": sample_portfolio.model_dump(),
        "symbol": "PLTR",
    }
    # snapshot.timestamp needs ISO string
    payload["snapshot"]["timestamp"] = sample_snapshot.timestamp.isoformat()

    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["overall_status"] == "PASSED"
    assert data["symbol"] == "PLTR"
    assert data["supported_count"] >= 5
    assert "claims" in data
    assert isinstance(data["claims"], list)


def test_api_trace_includes_evidence_audit(tmp_path, sample_snapshot, sample_portfolio, supported_proposal):
    """GET /api/trace/{trade_id} includes evidence_audit in JSON payload."""
    db_path = str(tmp_path / "api_trace_test.db")
    journal = TradeJournal(db_path=db_path)

    tid = uuid.uuid4()
    record = TradeRecord(
        trade_id=tid,
        thesis_id=uuid.uuid4(),
        symbol="PLTR",
        action=Action.BUY,
        quantity=1.0,
        original_thesis=(
            f"{supported_proposal.thesis}\n\nEvidence:\n{supported_proposal.evidence}"
            f"\n\nSummary:\n{supported_proposal.reasoning_summary}"
        ),
        original_confidence=0.85,
        assumptions=supported_proposal.assumptions,
        invalidation_conditions=supported_proposal.invalidation_conditions,
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=186.24,
    )
    journal.save_record(record)

    auditor = EvidenceAuditor()
    audit_report = auditor.audit(record, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    journal.save_evidence_audit(audit_report, trade_id=tid)

    with patch("tradepilot.api.DB_PATH", db_path):
        with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
            with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
                client = TestClient(app)
                res = client.get(f"/api/trace/{tid}")
                assert res.status_code == 200
                json_data = res.json()
                assert "evidence_audit" in json_data
                assert json_data["evidence_audit"] is not None
                assert json_data["evidence_audit"]["overall_status"] == "PASSED"


# ── 11. Decision Trace CLI Output ─────────────────────────────────────────────

def test_decision_trace_cli_prints_evidence_audit(tmp_path, sample_snapshot, supported_proposal, capsys):
    """Decision trace CLI includes section 20 and summary verification."""
    db_path = str(tmp_path / "cli_trace_test.db")
    journal = TradeJournal(db_path=db_path)

    tid = uuid.uuid4()
    record = TradeRecord(
        trade_id=tid,
        thesis_id=uuid.uuid4(),
        symbol="PLTR",
        action=Action.BUY,
        quantity=1.0,
        original_thesis=(
            f"{supported_proposal.thesis}\n\nEvidence:\n{supported_proposal.evidence}"
            f"\n\nSummary:\n{supported_proposal.reasoning_summary}"
        ),
        original_confidence=0.85,
        assumptions=supported_proposal.assumptions,
        invalidation_conditions=supported_proposal.invalidation_conditions,
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=186.24,
    )
    journal.save_record(record)

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            exit_code = build_trace(journal, trade_id_str=str(tid))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "20 . AI DECISION EVIDENCE AUDIT" in out
    assert "Overall Status" in out
    assert "Was the decision evidence verified?" in out


# ── 12. Negation Handling (BUG FIX) ───────────────────────────────────────────

def test_negated_above_sma20_does_not_extract_positive_claim(sample_snapshot, sample_portfolio):
    """'Price is not above SMA20' must NOT produce a positive relational claim."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, ClaimStatus, StrategyProposal
    proposal = StrategyProposal(
        action=Action.SELL, confidence=0.7,
        thesis="Price is not above SMA20, it has broken down below that level.",
        evidence="Price: 186.24, SMA20: 171.03",
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=190.0, reasoning_summary="Bearish breakdown.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    above_claims = [c for c in report.claims if c.claim == "Price is trading above SMA20"]
    assert above_claims == [], (
        f"Negated 'not above SMA20' must not produce a positive above-SMA20 claim. Got: {above_claims}"
    )


def test_negated_above_sma50_does_not_extract_positive_claim(sample_snapshot, sample_portfolio):
    """'not above SMA50' and 'not above SMA20' must NOT produce positive relational claims."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, StrategyProposal
    proposal = StrategyProposal(
        action=Action.SELL, confidence=0.7,
        thesis="Price is not above SMA50, and definitely not above SMA20.",
        evidence="Price: 186.24, SMA50: 143.74, SMA20: 171.03",
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=190.0, reasoning_summary="Bearish.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    assert [c for c in report.claims if c.claim == "Price is trading above SMA50"] == [], \
        "Negated 'not above SMA50' must not produce a positive above-SMA50 claim"
    assert [c for c in report.claims if c.claim == "Price is trading above SMA20"] == [], \
        "Negated 'not above SMA20' must not produce a positive above-SMA20 claim"


# ── 13. Contradictory Price Claim (BUG FIX) ──────────────────────────────────

def test_contradictory_price_claims_both_extracted_and_one_fails(sample_snapshot, sample_portfolio):
    """When two different price values appear, both must be extracted; wrong one causes FAILED."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, AuditStatus, ClaimStatus, StrategyProposal
    proposal = StrategyProposal(
        action=Action.BUY, confidence=0.8,
        thesis="Price: 186.24",
        evidence="Price: 186.24, Price: 210.00",  # contradictory second value
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=170.0, reasoning_summary="Contradictory.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    price_claims = [c for c in report.claims if c.evidence_source == "MarketSnapshot.current_price"]
    assert len(price_claims) == 2, (
        f"Both price claims (186.24 and 210.00) must be extracted; "
        f"got {[(c.claimed_value, c.status.value) for c in price_claims]}"
    )
    assert any(c.status == ClaimStatus.SUPPORTED for c in price_claims), "186.24 should be SUPPORTED"
    assert any(c.status == ClaimStatus.UNSUPPORTED for c in price_claims), "210.00 should be UNSUPPORTED"
    assert report.overall_status == AuditStatus.FAILED, (
        f"Expected FAILED due to contradictory price; got {report.overall_status}"
    )


# ── 14. Extraction Failure Reporting (BUG FIX) ────────────────────────────────

def test_unparseable_price_value_emits_unverifiable_claim(sample_snapshot, sample_portfolio):
    """'Price: N/A' must emit an explicit UNVERIFIABLE extraction-failure claim."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, StrategyProposal
    proposal = StrategyProposal(
        action=Action.BUY, confidence=0.5,
        thesis="Price: N/A. Data feed temporarily unavailable.",
        evidence="SMA20: unknown",
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=None, reasoning_summary="Data error.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    all_claim_text = " ".join(c.claim for c in report.claims)
    assert "extraction failed" in all_claim_text.lower() or "unparseable" in all_claim_text.lower(), (
        f"Unparseable values must emit explicit UNVERIFIABLE claims. "
        f"Got: {[c.claim for c in report.claims]}"
    )
    assert report.unsupported_count == 0, "Parse failures must be UNVERIFIABLE, not UNSUPPORTED"


# ── 15. Missing Indicator Detection (BUG FIX) ─────────────────────────────────

def test_rsi_claim_is_flagged_unverifiable(sample_snapshot, sample_portfolio):
    """RSI: 65 in a proposal must be flagged UNVERIFIABLE (not in MarketSnapshot)."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, ClaimStatus, StrategyProposal
    proposal = StrategyProposal(
        action=Action.BUY, confidence=0.8,
        thesis="RSI: 65 indicates bullish momentum. Price: 186.24.",
        evidence="RSI: 65, Price: 186.24",
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=170.0, reasoning_summary="RSI play.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    rsi_claims = [c for c in report.claims if "RSI" in c.claim]
    assert len(rsi_claims) >= 1, "RSI claim must be explicitly extracted as UNVERIFIABLE"
    assert all(c.status == ClaimStatus.UNVERIFIABLE for c in rsi_claims), \
        "RSI must be UNVERIFIABLE (absent from MarketSnapshot)"


def test_vwap_and_macd_claims_are_flagged_unverifiable(sample_snapshot, sample_portfolio):
    """VWAP: 180.0 and MACD: 2.5 must be flagged UNVERIFIABLE."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import Action, ClaimStatus, StrategyProposal
    proposal = StrategyProposal(
        action=Action.BUY, confidence=0.8,
        thesis="VWAP: 180.0 and MACD: 2.5 confirm bullish setup. Price: 186.24.",
        evidence="VWAP: 180.0, MACD: 2.5, Price: 186.24",
        assumptions=[], invalidation_conditions=[],
        suggested_stop_loss=170.0, reasoning_summary="VWAP/MACD play.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    vwap_claims = [c for c in report.claims if "VWAP" in c.claim]
    macd_claims = [c for c in report.claims if "MACD" in c.claim]
    assert len(vwap_claims) >= 1, "VWAP claim must be explicitly extracted"
    assert len(macd_claims) >= 1, "MACD claim must be explicitly extracted"
    assert all(c.status == ClaimStatus.UNVERIFIABLE for c in vwap_claims), "VWAP must be UNVERIFIABLE"
    assert all(c.status == ClaimStatus.UNVERIFIABLE for c in macd_claims), "MACD must be UNVERIFIABLE"


# ── 16. Decision Trace Circular Audit Guard (BUG FIX) ────────────────────────

def test_decision_trace_without_saved_audit_reports_missing_evidence(tmp_path, supported_proposal):
    """When no saved audit exists, get_trace_data must return MISSING_EVIDENCE not a circular pass."""
    import uuid
    from unittest.mock import patch
    from tradepilot import decision_trace as dt
    from tradepilot.core.models import Action, AuditStatus, ExecutionStatus, RiskStatus, TradeRecord
    from tradepilot.memory.journal import TradeJournal

    db_path = str(tmp_path / "no_audit_test.db")
    journal = TradeJournal(db_path=db_path)
    tid = uuid.uuid4()
    record = TradeRecord(
        trade_id=tid, thesis_id=uuid.uuid4(), symbol="PLTR", action=Action.BUY,
        quantity=1.0,
        original_thesis=(
            supported_proposal.thesis + "\n\nEvidence:\n" + supported_proposal.evidence
            + "\n\nSummary:\n" + supported_proposal.reasoning_summary
        ),
        original_confidence=0.85,
        assumptions=supported_proposal.assumptions,
        invalidation_conditions=supported_proposal.invalidation_conditions,
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=186.24,
    )
    journal.save_record(record)

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            data = dt.get_trace_data(journal, trade_id_str=str(tid))

    assert data is not None
    ea = data.get("evidence_audit")
    assert ea is not None, "evidence_audit must be present even when no saved audit"
    assert ea.overall_status == AuditStatus.MISSING_EVIDENCE, (
        f"Expected MISSING_EVIDENCE (no real snapshot available), got {ea.overall_status}"
    )
    assert ea.evidence_available is False


# ── 17. Repeated Audits Preserve Distinct Records (confirmed correct behavior) ─

def test_repeated_audits_are_preserved_as_distinct_records(tmp_journal, sample_snapshot, supported_proposal):
    """Repeated audits of the same trade_id create distinct records, all retrievable."""
    import uuid
    from tradepilot.agents.evidence_auditor import EvidenceAuditor

    auditor = EvidenceAuditor()
    tid = uuid.uuid4()
    r1 = auditor.audit(supported_proposal, snapshot=sample_snapshot, symbol="PLTR")
    r2 = auditor.audit(supported_proposal, snapshot=sample_snapshot, symbol="PLTR")
    tmp_journal.save_evidence_audit(r1, trade_id=tid)
    tmp_journal.save_evidence_audit(r2, trade_id=tid)

    assert r1.audit_id != r2.audit_id, "Each audit invocation must produce a unique audit_id"

    recent = tmp_journal.get_evidence_audit_by_trade_id(tid)
    assert recent is not None and recent.audit_id == r2.audit_id, "Most recent audit must be returned"

    assert tmp_journal.get_evidence_audit(r1.audit_id) is not None, "r1 must be fetchable by audit_id"
    assert tmp_journal.get_evidence_audit(r2.audit_id) is not None, "r2 must be fetchable by audit_id"

    all_ids = {a.audit_id for a in tmp_journal.list_evidence_audits()}
    assert r1.audit_id in all_ids, "r1 must appear in list_evidence_audits"
    assert r2.audit_id in all_ids, "r2 must appear in list_evidence_audits"


# ── 18. PendingProposal Representation and Precedence Order Regressions ──────

def test_audit_pending_proposal_shape(sample_snapshot, sample_portfolio):
    """PendingProposal shape is cleanly extracted and audited."""
    from tradepilot.agents.evidence_auditor import EvidenceAuditor
    from tradepilot.core.models import PendingProposal, TradeProposal

    t_prop = TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="PLTR",
        action=Action.BUY,
        quantity=10.0,
        current_price=186.24,
        stop_loss=170.0,
        confidence=0.85,
        reasoning=(
            "PLTR is in an uptrend with price at 186.24.\n\n"
            "Evidence:\nPrice: 186.24, SMA20: 171.03, Cash: 10000\n\n"
            "Summary:\nBullish alignment."
        ),
        assumptions=["Momentum continues."],
        invalidation_conditions=["Price drops below 170.0."],
    )
    pending = PendingProposal(
        trade_id=t_prop.trade_id,
        thesis_id=t_prop.thesis_id,
        symbol="PLTR",
        action=Action.BUY,
        quantity=10.0,
        reference_price=186.24,
        stop_loss=170.0,
        snapshot_timestamp=sample_snapshot.timestamp,
        risk_decision=RiskStatus.APPROVED,
        risk_reasons=["Risk within limits."],
        proposal_snapshot=t_prop,
    )

    auditor = EvidenceAuditor()
    report = auditor.audit(pending, snapshot=sample_snapshot, portfolio=sample_portfolio)

    assert report.symbol == "PLTR"
    assert report.proposal_action == Action.BUY
    assert report.overall_status == AuditStatus.PASSED
    assert report.supported_count >= 2
    assert report.unsupported_count == 0


def test_precedence_missing_evidence_over_mismatch(sample_portfolio, supported_proposal):
    """MISSING_EVIDENCE takes precedence over symbol or timestamp mismatch when snapshot is None."""
    auditor = EvidenceAuditor()
    report = auditor.audit(
        supported_proposal,
        snapshot=None,
        portfolio=sample_portfolio,
        symbol="MSFT",  # mismatch with proposal symbol PLTR
    )
    assert report.overall_status == AuditStatus.MISSING_EVIDENCE


def test_precedence_stale_or_mismatch_over_failed(sample_snapshot, sample_portfolio):
    """STALE_OR_MISMATCHED takes precedence over unsupported factual claims."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Wrong price claim.",
        evidence="Price: 999.99",  # Contradicts snapshot current_price 186.24
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="Mismatch test.",
    )
    auditor = EvidenceAuditor()
    # Mismatched symbol (AAPL vs PLTR snapshot)
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="AAPL")
    assert report.overall_status == AuditStatus.STALE_OR_MISMATCHED


def test_precedence_failed_over_inconclusive(sample_snapshot, sample_portfolio):
    """FAILED takes precedence over INCONCLUSIVE when an unsupported claim coexists with evidence gaps."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Contradicted price with absent indicator.",
        evidence="Price: 999.99, RSI: 65",  # Price contradicts snapshot, RSI is absent indicator
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="Precedence test.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    # Unsupported claim must produce FAILED, not INCONCLUSIVE
    assert report.overall_status == AuditStatus.FAILED
    assert report.unsupported_count >= 1
    assert any("RSI" in c.claim for c in report.claims if c.status == ClaimStatus.UNVERIFIABLE)


def test_precedence_inconclusive_over_passed(sample_snapshot, sample_portfolio):
    """INCONCLUSIVE takes precedence over PASSED when a factual evidence gap exists."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Valid price with absent indicator.",
        evidence="Price: 186.24, RSI: 65",  # Price matches snapshot, RSI is absent
        assumptions=[],
        invalidation_conditions=[],
        suggested_stop_loss=170.0,
        reasoning_summary="Gap test.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    # Missing indicator is a factual evidence gap -> INCONCLUSIVE
    assert report.overall_status == AuditStatus.INCONCLUSIVE
    assert report.supported_count >= 1
    assert report.unsupported_count == 0


def test_passed_with_qualitative_assumptions(sample_snapshot, sample_portfolio):
    """Qualitative assumptions do not degrade an otherwise supported audit to INCONCLUSIVE."""
    proposal = StrategyProposal(
        action=Action.BUY,
        confidence=0.8,
        thesis="Valid price setup.",
        evidence="Price: 186.24, SMA20: 171.03",
        assumptions=[
            "Market liquidity will remain strong.",
            "Sector rotation favors tech equities.",
        ],
        invalidation_conditions=["Unforeseen macro shocks."],
        suggested_stop_loss=170.0,
        reasoning_summary="Assumptions test.",
    )
    auditor = EvidenceAuditor()
    report = auditor.audit(proposal, snapshot=sample_snapshot, portfolio=sample_portfolio, symbol="PLTR")
    assert report.overall_status == AuditStatus.PASSED
    assert report.supported_count >= 2
    assert report.unsupported_count == 0
    assert report.unverifiable_count >= 3

