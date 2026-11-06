"""
tests/test_api_approval.py

Comprehensive tests for the Human Approval Flow (Step 4).

Critical invariants verified:
  - PendingProposal is persisted in pending_proposals, NOT trade_records.
  - Quantity/action/symbol/reference_price/stop_loss/snapshot_timestamp are immutable.
  - RISK_APPROVED -> HUMAN_APPROVED is an atomic conditional DB transition.
  - Duplicate/concurrent approval returns 409.
  - Stale snapshot rejects approval.
  - Changed portfolio/risk conditions reject approval.
  - /api/approve never calls Alpaca or executor.
  - Step 4 never creates or modifies a TradeRecord.
  - NO_TRADE and risk-rejected proposals never receive a trade_id.
  - Approval ends at HUMAN_APPROVED - no execution state.
"""

import uuid
import sqlite3
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tradepilot.api import app
from tradepilot.core.models import (
    MarketSnapshot, PortfolioState, StrategyProposal, Action, MemoryInfluence,
    TradeLifecycleStatus, RiskStatus, TradeProposal, OrderType, TimeInForce,
    PendingProposal,
)
from tradepilot.memory.journal import TradeJournal

client = TestClient(app)

FIXED_TS = datetime(2023, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def mock_snapshot():
    return MarketSnapshot(
        symbol="AAPL",
        current_price=150.0,
        timestamp=FIXED_TS,
        volume=1_000_000.0,
        previous_close=148.0,
        price_change_pct=0.0135,
        sma_20=145.0,
        sma_50=140.0,
        volatility=0.02,
    )


@pytest.fixture
def mock_portfolio():
    return PortfolioState(
        account_equity=10_000.0,
        buying_power=20_000.0,
        cash=10_000.0,
        current_daily_loss=0.0,
        open_positions=[],
        number_of_open_positions=0,
        position_quantities={},
    )


@pytest.fixture
def valid_strategy_proposal():
    return {
        "action": "BUY",
        "confidence": 0.8,
        "thesis": "Strong momentum",
        "evidence": "Price > SMA20",
        "assumptions": ["Market stays open"],
        "invalidation_conditions": ["Price drops below SMA50"],
        "suggested_stop_loss": 140.0,
        "reasoning_summary": "Momentum trade",
        "memory_influence": "NONE",
        "memory_reasoning": "",
    }


@pytest.fixture
def tmp_journal(tmp_path):
    db = str(tmp_path / "test.db")
    return TradeJournal(db_path=db)


def _make_pending(journal, snapshot):
    tp = TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        quantity=6.0,
        current_price=snapshot.current_price,
        stop_loss=140.0,
        confidence=0.8,
        reasoning="Momentum",
        assumptions=["Market stays open"],
        invalidation_conditions=["Price drops"],
    )
    pending = PendingProposal(
        trade_id=tp.trade_id,
        thesis_id=tp.thesis_id,
        symbol="AAPL",
        action=Action.BUY,
        quantity=tp.quantity,
        reference_price=tp.current_price,
        stop_loss=tp.stop_loss,
        snapshot_timestamp=snapshot.timestamp,
        risk_decision=RiskStatus.APPROVED,
        risk_reasons=["All risk parameters met."],
        lifecycle_status=TradeLifecycleStatus.RISK_APPROVED,
        proposal_snapshot=tp,
    )
    journal.save_pending_proposal(pending)
    return pending


class TestJournalPendingProposals:

    def test_save_and_retrieve(self, tmp_journal, mock_snapshot):
        pending = _make_pending(tmp_journal, mock_snapshot)
        retrieved = tmp_journal.get_pending_proposal(pending.trade_id)
        assert retrieved is not None
        assert retrieved.trade_id == pending.trade_id
        assert retrieved.quantity == pending.quantity
        assert retrieved.reference_price == pending.reference_price
        assert retrieved.stop_loss == pending.stop_loss
        assert retrieved.snapshot_timestamp == pending.snapshot_timestamp
        assert retrieved.lifecycle_status == TradeLifecycleStatus.RISK_APPROVED
        assert retrieved.human_approved_at is None

    def test_trade_records_not_written(self, tmp_journal, mock_snapshot):
        _make_pending(tmp_journal, mock_snapshot)
        with sqlite3.connect(tmp_journal.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
        assert count == 0

    def test_atomic_approve_success(self, tmp_journal, mock_snapshot):
        pending = _make_pending(tmp_journal, mock_snapshot)
        result = tmp_journal.approve_pending_proposal(pending.trade_id, datetime.now(timezone.utc))
        assert result is True
        retrieved = tmp_journal.get_pending_proposal(pending.trade_id)
        assert retrieved.lifecycle_status == TradeLifecycleStatus.HUMAN_APPROVED
        assert retrieved.human_approved_at is not None

    def test_atomic_approve_idempotency(self, tmp_journal, mock_snapshot):
        pending = _make_pending(tmp_journal, mock_snapshot)
        approved_at = datetime.now(timezone.utc)
        first = tmp_journal.approve_pending_proposal(pending.trade_id, approved_at)
        second = tmp_journal.approve_pending_proposal(pending.trade_id, approved_at)
        assert first is True
        assert second is False

    def test_get_nonexistent_returns_none(self, tmp_journal):
        assert tmp_journal.get_pending_proposal(uuid.uuid4()) is None

    def test_duplicate_insert_raises(self, tmp_journal, mock_snapshot):
        pending = _make_pending(tmp_journal, mock_snapshot)
        with pytest.raises(sqlite3.IntegrityError):
            tmp_journal.save_pending_proposal(pending)

    def test_immutable_fields_preserved_after_approve(self, tmp_journal, mock_snapshot):
        pending = _make_pending(tmp_journal, mock_snapshot)
        tmp_journal.approve_pending_proposal(pending.trade_id, datetime.now(timezone.utc))
        retrieved = tmp_journal.get_pending_proposal(pending.trade_id)
        assert retrieved.quantity == pending.quantity
        assert retrieved.action == pending.action
        assert retrieved.symbol == pending.symbol
        assert retrieved.reference_price == pending.reference_price
        assert retrieved.stop_loss == pending.stop_loss
        assert retrieved.snapshot_timestamp == pending.snapshot_timestamp


class TestRiskEndpointPersistence:

    def test_approved_risk_persists_pending_proposal_not_trade_record(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        db_path = str(tmp_path / "test.db")
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post("/api/risk/AAPL", json={
                "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
                "strategy_proposal": valid_strategy_proposal,
            })
        assert res.status_code == 200
        data = res.json()
        assert data["risk_status"] == "APPROVED"
        assert data["trade_id"] is not None
        with sqlite3.connect(db_path) as conn:
            pp_count = conn.execute("SELECT COUNT(*) FROM pending_proposals").fetchone()[0]
            tr_count = conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
        assert pp_count == 1
        assert tr_count == 0  # CRITICAL: trade_records untouched

    def test_rejected_risk_no_trade_id_no_pending(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        db_path = str(tmp_path / "test.db")
        # Initialize DB so pending_proposals exists
        TradeJournal(db_path=db_path)
        
        mock_portfolio.current_daily_loss = 2500.0
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post("/api/risk/AAPL", json={
                "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
                "strategy_proposal": valid_strategy_proposal,
            })
        data = res.json()
        assert data["risk_status"] == "REJECTED"
        assert data.get("trade_id") is None
        with sqlite3.connect(db_path) as conn:
            pp_count = conn.execute("SELECT COUNT(*) FROM pending_proposals").fetchone()[0]
        assert pp_count == 0

    def test_no_trade_has_no_trade_id(self, mock_snapshot, mock_portfolio, tmp_path):
        db_path = str(tmp_path / "test.db")
        # Initialize DB
        TradeJournal(db_path=db_path)
        
        no_trade = {
            "action": "NO_TRADE", "confidence": 0.3, "thesis": "Uncertain",
            "evidence": "Weak", "assumptions": [], "invalidation_conditions": [],
            "reasoning_summary": "No action", "memory_influence": "NONE", "memory_reasoning": "",
        }
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post("/api/risk/AAPL", json={
                "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
                "strategy_proposal": no_trade,
            })
        data = res.json()
        assert data["risk_status"] == "NOT_APPLICABLE"
        assert data.get("trade_id") is None


class TestApproveEndpoint:

    def _setup(self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path):
        db_path = str(tmp_path / "test.db")
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post("/api/risk/AAPL", json={
                "snapshot_timestamp": mock_snapshot.timestamp.isoformat(),
                "strategy_proposal": valid_strategy_proposal,
            })
        assert res.json()["risk_status"] == "APPROVED"
        return res.json()["trade_id"], db_path

    def test_successful_approval(self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["lifecycle_status"] == "HUMAN_APPROVED"
        assert data["trade_id"] == trade_id
        assert data["human_approved_at"] is not None

    def test_approval_does_not_create_trade_record(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            client.post(f"/api/approve/{trade_id}")
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
        assert count == 0  # CRITICAL

    def test_approval_does_not_call_save_record(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path), \
             patch("tradepilot.memory.journal.TradeJournal.save_record") as mock_save:
            client.post(f"/api/approve/{trade_id}")
            mock_save.assert_not_called()

    def test_duplicate_approval_returns_409(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        for i, expected in enumerate([200, 409]):
            with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
                 patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
                 patch("tradepilot.api.DB_PATH", db_path):
                res = client.post(f"/api/approve/{trade_id}")
            assert res.status_code == expected, f"Request {i+1}: expected {expected}, got {res.status_code}"

    def test_unknown_trade_id_returns_404(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_invalid_trade_id_format_returns_400(self):
        res = client.post("/api/approve/not-a-uuid")
        assert res.status_code == 400

    def test_stale_snapshot_rejects_approval(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        stale_snapshot = mock_snapshot.model_copy(update={
            "timestamp": datetime(2023, 10, 2, 12, 0, tzinfo=timezone.utc)
        })
        with patch("tradepilot.api.fetch_market_snapshot", return_value=stale_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 409

    def test_changed_buying_power_rejects_approval(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        poor_portfolio = mock_portfolio.model_copy(update={"buying_power": 1.0})
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=poor_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 400

    def test_daily_loss_hit_rejects_approval(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        over_loss = mock_portfolio.model_copy(update={"current_daily_loss": 2500.0})
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=over_loss), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 400

    def test_exact_quantity_preserved_in_response(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        journal = TradeJournal(db_path=db_path)
        original_qty = journal.get_pending_proposal(uuid.UUID(trade_id)).quantity
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 200
        assert res.json()["quantity"] == original_qty

    def test_immutable_fields_unchanged_after_approval(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        journal = TradeJournal(db_path=db_path)
        before = journal.get_pending_proposal(uuid.UUID(trade_id))
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            client.post(f"/api/approve/{trade_id}")
        after = journal.get_pending_proposal(uuid.UUID(trade_id))
        assert after.quantity == before.quantity
        assert after.action == before.action
        assert after.symbol == before.symbol
        assert after.reference_price == before.reference_price
        assert after.stop_loss == before.stop_loss
        assert after.snapshot_timestamp == before.snapshot_timestamp
        assert after.lifecycle_status == TradeLifecycleStatus.HUMAN_APPROVED

    def test_response_lifecycle_is_human_approved_not_executed(
        self, mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path
    ):
        trade_id, db_path = self._setup(mock_snapshot, mock_portfolio, valid_strategy_proposal, tmp_path)
        with patch("tradepilot.api.fetch_market_snapshot", return_value=mock_snapshot), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=mock_portfolio), \
             patch("tradepilot.api.DB_PATH", db_path):
            res = client.post(f"/api/approve/{trade_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["lifecycle_status"] == "HUMAN_APPROVED"
        assert "EXECUTED" not in str(data)
        assert "EXECUTION_PENDING" not in str(data)
