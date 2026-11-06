
import uuid, sqlite3, json, pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from tradepilot.api import app
from tradepilot.core.models import (
    MarketSnapshot, PortfolioState, Action,
    TradeLifecycleStatus, RiskStatus, TradeProposal, OrderType, TimeInForce,
    PendingProposal, ApprovedTrade, TradeRecord, ExecutionStatus,
)
from tradepilot.memory.journal import TradeJournal

client = TestClient(app)
FIXED_TS = datetime(2023, 10, 1, 12, 0, tzinfo=timezone.utc)

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_snapshot():
    return MarketSnapshot(symbol="AAPL", current_price=150.0, timestamp=FIXED_TS,
        volume=1_000_000.0, previous_close=148.0, price_change_pct=0.0135,
        sma_20=145.0, sma_50=140.0, volatility=0.02)

@pytest.fixture
def mock_portfolio():
    return PortfolioState(account_equity=10_000.0, buying_power=20_000.0, cash=10_000.0,
        current_daily_loss=0.0, open_positions=[], number_of_open_positions=0,
        position_quantities={})

@pytest.fixture
def tmp_journal(tmp_path):
    return TradeJournal(db_path=str(tmp_path / "test.db"))

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _tp(trade_id=None, quantity=6.0, price=150.0):
    return TradeProposal(trade_id=trade_id or uuid.uuid4(), thesis_id=uuid.uuid4(),
        symbol="AAPL", action=Action.BUY, order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY, quantity=quantity, current_price=price,
        stop_loss=140.0, confidence=0.8, reasoning="Momentum",
        assumptions=["Mkt open"], invalidation_conditions=["Drop"])

def _mk(journal, snapshot, lc=TradeLifecycleStatus.RISK_APPROVED, qty=6.0):
    tp = _tp(quantity=qty, price=snapshot.current_price)
    p = PendingProposal(trade_id=tp.trade_id, thesis_id=tp.thesis_id, symbol="AAPL",
        action=Action.BUY, quantity=tp.quantity, reference_price=tp.current_price,
        stop_loss=tp.stop_loss, snapshot_timestamp=snapshot.timestamp,
        risk_decision=RiskStatus.APPROVED, risk_reasons=["OK"],
        lifecycle_status=lc, proposal_snapshot=tp)
    journal.save_pending_proposal(p)
    if lc != TradeLifecycleStatus.RISK_APPROVED:
        with sqlite3.connect(journal.db_path) as conn:
            row = conn.execute("SELECT proposal_data FROM pending_proposals WHERE trade_id=?",
                (str(tp.trade_id),)).fetchone()
            d = json.loads(row[0])
            d["lifecycle_status"] = lc.value
            if lc in (TradeLifecycleStatus.HUMAN_APPROVED, TradeLifecycleStatus.EXECUTED):
                d["human_approved_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE pending_proposals SET lifecycle_status=?,proposal_data=? WHERE trade_id=?",
                (lc.value, json.dumps(d), str(tp.trade_id)))
            conn.commit()
    return p

def _order(status="accepted", aid="alpaca-abc", fqty="0", fprice=None):
    o = MagicMock(); o.id = aid; o.status = status
    o.filled_qty = fqty; o.filled_avg_price = fprice
    return o

def _mc(status="accepted", aid="alpaca-abc", fqty="0", fprice=None):
    mc = MagicMock()
    mc.submit_order.return_value = _order(status, aid, fqty, fprice)
    return mc

# ─── Journal unit tests ───────────────────────────────────────────────────────

class TestJournalStep5:
    def test_transition_success(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        assert tmp_journal.transition_to_execution_pending(p.trade_id) is True
        assert tmp_journal.get_pending_proposal(p.trade_id).lifecycle_status == TradeLifecycleStatus.EXECUTION_PENDING

    def test_transition_idempotent_false(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        tmp_journal.transition_to_execution_pending(p.trade_id)
        assert tmp_journal.transition_to_execution_pending(p.trade_id) is False

    def test_transition_from_risk_approved_fails(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.RISK_APPROVED)
        assert tmp_journal.transition_to_execution_pending(p.trade_id) is False
        assert tmp_journal.get_pending_proposal(p.trade_id).lifecycle_status == TradeLifecycleStatus.RISK_APPROVED

    def test_mark_executed(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.EXECUTION_PENDING)
        tmp_journal.mark_proposal_executed(p.trade_id)
        assert tmp_journal.get_pending_proposal(p.trade_id).lifecycle_status == TradeLifecycleStatus.EXECUTED

    def test_mark_executed_idempotent(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.EXECUTED)
        tmp_journal.mark_proposal_executed(p.trade_id)
        assert tmp_journal.get_pending_proposal(p.trade_id).lifecycle_status == TradeLifecycleStatus.EXECUTED

    def test_json_blob_updated(self, tmp_journal, mock_snapshot):
        p = _mk(tmp_journal, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        tmp_journal.transition_to_execution_pending(p.trade_id)
        with sqlite3.connect(tmp_journal.db_path) as conn:
            row = conn.execute(
                "SELECT lifecycle_status,proposal_data FROM pending_proposals WHERE trade_id=?",
                (str(p.trade_id),)).fetchone()
        assert row[0] == "EXECUTION_PENDING"
        assert json.loads(row[1])["lifecycle_status"] == "EXECUTION_PENDING"

# ─── POST /api/execute/{trade_id} ─────────────────────────────────────────────

class TestExecuteEndpoint:
    def _setup(self, tmp_path, mock_snapshot, qty=6.0):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED, qty=qty)
        return str(p.trade_id), db, j, p

    # ── Success path ─────────────────────────────────────────────────────────

    def test_human_approved_executes(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        mc = _mc(aid="alpaca-xyz")
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            res = client.post(f"/api/execute/{tid}")
        assert res.status_code == 200
        d = res.json()
        assert d["lifecycle_status"] == "EXECUTED"
        assert d["alpaca_order_id"] == "alpaca-xyz"
        assert d["symbol"] == "AAPL" and d["action"] == "BUY"

    def test_pending_proposal_marked_executed(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True):
            client.post(f"/api/execute/{tid}")
        assert j.get_pending_proposal(uuid.UUID(tid)).lifecycle_status == TradeLifecycleStatus.EXECUTED

    def test_trade_record_created(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(aid="alpaca-rec"), create=True):
            client.post(f"/api/execute/{tid}")
        rec = j.get_record(uuid.UUID(tid))
        assert rec is not None and rec.alpaca_order_id == "alpaca-rec"

    def test_exact_quantity_preserved(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot, qty=6.0)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True):
            client.post(f"/api/execute/{tid}")
        assert j.get_record(uuid.UUID(tid)).quantity == 6.0

    # ── Lifecycle gates ───────────────────────────────────────────────────────

    def test_risk_approved_cannot_execute(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.RISK_APPROVED)
        with patch("tradepilot.api.DB_PATH", db):
            res = client.post(f"/api/execute/{str(p.trade_id)}")
        assert res.status_code == 400
        assert "Human approval" in res.json()["detail"]

    def test_risk_approved_no_alpaca_call(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.RISK_APPROVED)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute") as me:
            client.post(f"/api/execute/{str(p.trade_id)}")
            me.assert_not_called()

    def test_missing_proposal_404(self, tmp_path):
        db = str(tmp_path / "test.db")
        TradeJournal(db_path=db)
        with patch("tradepilot.api.DB_PATH", db):
            assert client.post(f"/api/execute/{uuid.uuid4()}").status_code == 404

    def test_invalid_trade_id_400(self):
        assert client.post("/api/execute/not-a-uuid").status_code == 400

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_already_executed_idempotent(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.EXECUTED)
        tp = p.proposal_snapshot
        j.save_record(TradeRecord(trade_id=p.trade_id, thesis_id=p.thesis_id,
            alpaca_order_id="existing", symbol=tp.symbol, action=tp.action,
            quantity=tp.quantity, original_thesis=tp.reasoning,
            original_confidence=tp.confidence, assumptions=tp.assumptions,
            invalidation_conditions=tp.invalidation_conditions,
            risk_decision=RiskStatus.APPROVED, execution_status=ExecutionStatus.FILLED))
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute") as me:
            res = client.post(f"/api/execute/{str(p.trade_id)}")
            me.assert_not_called()
        assert res.status_code == 200 and res.json().get("idempotent") is True

    def test_concurrent_duplicate_409(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        mc = _mc()
        with patch("tradepilot.api.DB_PATH", db), \
             patch.object(TradeJournal, "transition_to_execution_pending", return_value=False), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            res = client.post(f"/api/execute/{str(p.trade_id)}")
        assert res.status_code == 409

    # ── Frontend cannot override trade terms ──────────────────────────────────

    def test_frontend_cannot_override_quantity(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot, qty=6.0)
        captured = []
        mc = MagicMock()
        mc.submit_order.side_effect = lambda req: (captured.append(req), _order())[1]
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            res = client.post(f"/api/execute/{tid}", json={"quantity": 9999, "action": "SELL"})
        assert res.status_code == 200
        assert captured[0].qty == 6.0

    def test_frontend_cannot_override_price(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        captured = []
        mc = MagicMock()
        mc.submit_order.side_effect = lambda req: (captured.append(req), _order())[1]
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            res = client.post(f"/api/execute/{tid}", json={"limit_price": 999.99})
        assert res.status_code == 200
        req = captured[0]
        assert not hasattr(req, "limit_price") or req.limit_price is None

    def test_client_order_id_equals_trade_id(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        captured = []
        mc = MagicMock()
        mc.submit_order.side_effect = lambda req: (captured.append(req), _order())[1]
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            client.post(f"/api/execute/{tid}")
        assert captured[0].client_order_id == tid

    # ── No LLM, no re-sizing ──────────────────────────────────────────────────

    def test_no_llm_invoked(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True), \
             patch("tradepilot.agents.llm_strategy.LLMStrategyAgent.analyze_market") as ml:
            client.post(f"/api/execute/{tid}")
            ml.assert_not_called()

    def test_no_resizing(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True), \
             patch("tradepilot.api.calculate_position_size") as ms:
            client.post(f"/api/execute/{tid}")
            ms.assert_not_called()

    def test_no_market_fetch(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True), \
             patch("tradepilot.api.fetch_market_snapshot") as mf:
            client.post(f"/api/execute/{tid}")
            mf.assert_not_called()

    # ── Paper-only enforcement ────────────────────────────────────────────────

    def test_paper_only_enforcement(self, tmp_path, mock_snapshot, monkeypatch):
        from tradepilot.core.config import settings as s
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        monkeypatch.setattr(s, "alpaca_paper_trade", False)
        with patch("tradepilot.api.DB_PATH", db):
            res = client.post(f"/api/execute/{tid}")
        assert res.status_code == 500

    # ── Alpaca order outcomes ─────────────────────────────────────────────────

    def test_alpaca_filled(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client",
                   _mc("filled", "af", "6", "151.50"), create=True):
            res = client.post(f"/api/execute/{tid}")
        d = res.json()
        assert d["execution_status"] == "FILLED" and d["filled_quantity"] == 6.0
        assert d["average_fill_price"] == pytest.approx(151.50)

    def test_alpaca_rejected(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc("rejected"), create=True):
            res = client.post(f"/api/execute/{tid}")
        assert res.status_code == 200 and res.json()["execution_status"] == "REJECTED"
        assert j.get_record(uuid.UUID(tid)).execution_status == ExecutionStatus.REJECTED

    def test_alpaca_api_error(self, tmp_path, mock_snapshot):
        from alpaca.common.exceptions import APIError
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        mc = MagicMock()
        mc.submit_order.side_effect = APIError("down")
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            res = client.post(f"/api/execute/{tid}")
        assert res.status_code == 200 and res.json()["execution_status"] == "REJECTED"
        assert "Alpaca API Error" in res.json()["message"]

    def test_partial_fill(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client",
                   _mc("partially_filled", fprice="150.25", fqty="3"), create=True):
            res = client.post(f"/api/execute/{tid}")
        d = res.json()
        assert d["execution_status"] == "PARTIALLY_FILLED" and d["filled_quantity"] == 3.0

    def test_accepted_order(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc("accepted"), create=True):
            assert client.post(f"/api/execute/{tid}").json()["execution_status"] == "ACCEPTED"

    def test_pending_new_maps_pending(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc("pending_new"), create=True):
            assert client.post(f"/api/execute/{tid}").json()["execution_status"] == "PENDING"

    def test_missing_credentials(self, tmp_path, mock_snapshot, monkeypatch):
        from tradepilot.core.config import settings as s
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        monkeypatch.setattr(s, "alpaca_api_key", "")
        monkeypatch.setattr(s, "alpaca_secret_key", "")
        with patch("tradepilot.api.DB_PATH", db):
            res = client.post(f"/api/execute/{tid}")
        assert res.status_code == 200 and res.json()["execution_status"] == "REJECTED"
        assert "Missing Alpaca credentials" in res.json()["message"]

    # ── Historical record preservation ────────────────────────────────────────

    def test_historical_pltr_not_overwritten(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        hid = uuid.uuid4()
        j.save_record(TradeRecord(trade_id=hid, thesis_id=uuid.uuid4(),
            alpaca_order_id="hist", symbol="PLTR", action=Action.BUY,
            quantity=10, filled_quantity=10.0, average_fill_price=25.50, entry_price=25.50,
            original_thesis="h", original_confidence=0.85,
            assumptions=[], invalidation_conditions=[],
            risk_decision=RiskStatus.APPROVED, execution_status=ExecutionStatus.FILLED))
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(aid="new-aapl"), create=True):
            res = client.post(f"/api/execute/{str(p.trade_id)}")
        assert res.status_code == 200
        hist = j.get_record(hid)
        assert hist.symbol == "PLTR" and hist.average_fill_price == 25.50
        assert hist.execution_status == ExecutionStatus.FILLED
        assert j.get_record(p.trade_id).symbol == "AAPL"

    def test_two_records_coexist(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        j.save_record(TradeRecord(trade_id=uuid.uuid4(), thesis_id=uuid.uuid4(),
            symbol="PLTR", action=Action.BUY, quantity=10,
            original_thesis="h", original_confidence=0.9,
            assumptions=[], invalidation_conditions=[],
            risk_decision=RiskStatus.APPROVED, execution_status=ExecutionStatus.FILLED))
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.HUMAN_APPROVED)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True):
            client.post(f"/api/execute/{str(p.trade_id)}")
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0] == 2

    # ── EXECUTION_PENDING crash-recovery ──────────────────────────────────────

    def test_execution_pending_with_record_idempotent(self, tmp_path, mock_snapshot):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        p = _mk(j, mock_snapshot, TradeLifecycleStatus.EXECUTION_PENDING)
        tp = p.proposal_snapshot
        j.save_record(TradeRecord(trade_id=p.trade_id, thesis_id=p.thesis_id,
            alpaca_order_id="existing", symbol=tp.symbol, action=tp.action,
            quantity=tp.quantity, original_thesis=tp.reasoning,
            original_confidence=tp.confidence, assumptions=tp.assumptions,
            invalidation_conditions=tp.invalidation_conditions,
            risk_decision=RiskStatus.APPROVED, execution_status=ExecutionStatus.ACCEPTED))
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute") as me:
            res = client.post(f"/api/execute/{str(p.trade_id)}")
            me.assert_not_called()
        assert res.status_code == 200 and res.json().get("idempotent") is True

    # ── Exact terms sent to executor ──────────────────────────────────────────

    def test_exact_terms_sent_to_executor(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot, qty=6.0)
        captured = []

        def fake(self_inner, at):
            captured.append(at)
            from tradepilot.core.models import ExecutionResult, ExecutionStatus
            return ExecutionResult(trade_id=at.trade_id, thesis_id=at.proposal.thesis_id,
                client_order_id=at.trade_id, alpaca_order_id="fake",
                status=ExecutionStatus.ACCEPTED, symbol=at.proposal.symbol,
                action=at.proposal.action, quantity=at.proposal.quantity,
                order_type=at.proposal.order_type, message="ok")

        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.execute", fake):
            res = client.post(f"/api/execute/{tid}")

        assert res.status_code == 200 and len(captured) == 1
        at = captured[0]
        assert str(at.trade_id) == tid
        assert at.proposal.symbol == "AAPL" and at.proposal.action == Action.BUY
        assert at.proposal.quantity == 6.0 and at.proposal.order_type == OrderType.MARKET

    def test_response_required_fields(self, tmp_path, mock_snapshot):
        tid, db, j, p = self._setup(tmp_path, mock_snapshot)
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", _mc(), create=True):
            d = client.post(f"/api/execute/{tid}").json()
        for f in ["trade_id", "thesis_id", "symbol", "action", "quantity", "order_type",
                  "client_order_id", "alpaca_order_id", "execution_status", "filled_quantity",
                  "lifecycle_status", "message", "submitted_timestamp"]:
            assert f in d, f"Missing required field: {f}"


# ─── Full lifecycle integration ───────────────────────────────────────────────

class TestFullLifecycle:
    STRATEGY = {"action": "BUY", "confidence": 0.8, "thesis": "Momentum",
        "evidence": "Price>SMA20", "assumptions": ["Mkt open"],
        "invalidation_conditions": ["Drop"], "suggested_stop_loss": 140.0,
        "reasoning_summary": "Momentum trade", "memory_influence": "NONE",
        "memory_reasoning": ""}

    def test_full_flow_risk_approve_execute(self, tmp_path):
        db = str(tmp_path / "test.db")
        snap = MarketSnapshot(symbol="AAPL", current_price=150.0, timestamp=FIXED_TS,
            volume=1_000_000.0, previous_close=148.0, price_change_pct=0.0135,
            sma_20=145.0, sma_50=140.0, volatility=0.02)
        port = PortfolioState(account_equity=10_000.0, buying_power=20_000.0, cash=10_000.0,
            current_daily_loss=0.0, open_positions=[], number_of_open_positions=0,
            position_quantities={})

        # Step 3: Risk
        with patch("tradepilot.api.fetch_market_snapshot", return_value=snap), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=port), \
             patch("tradepilot.api.DB_PATH", db):
            rr = client.post("/api/risk/AAPL", json={
                "snapshot_timestamp": snap.timestamp.isoformat(),
                "strategy_proposal": self.STRATEGY})
        assert rr.json()["risk_status"] == "APPROVED"
        tid = rr.json()["trade_id"]

        # Step 4: Approve
        with patch("tradepilot.api.fetch_market_snapshot", return_value=snap), \
             patch("tradepilot.api.fetch_portfolio_state", return_value=port), \
             patch("tradepilot.api.DB_PATH", db):
            ar = client.post(f"/api/approve/{tid}")
        assert ar.json()["lifecycle_status"] == "HUMAN_APPROVED"

        # Step 5: Execute
        mc = _mc("accepted", "full-alpaca")
        with patch("tradepilot.api.DB_PATH", db), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.__init__", return_value=None), \
             patch("tradepilot.alpaca.execution.AlpacaPaperExecutor.client", mc, create=True):
            er = client.post(f"/api/execute/{tid}")

        assert er.status_code == 200
        ed = er.json()
        assert ed["lifecycle_status"] == "EXECUTED"
        assert ed["execution_status"] == "ACCEPTED"
        assert ed["alpaca_order_id"] == "full-alpaca"
        assert ed["quantity"] == 6.0

        j = TradeJournal(db_path=db)
        assert j.get_pending_proposal(uuid.UUID(tid)).lifecycle_status == TradeLifecycleStatus.EXECUTED
        rec = j.get_record(uuid.UUID(tid))
        assert rec is not None and rec.execution_status == ExecutionStatus.ACCEPTED

        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM pending_proposals").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0] == 1
