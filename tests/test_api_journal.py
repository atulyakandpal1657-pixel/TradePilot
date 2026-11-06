import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch

from tradepilot.api import app
from tradepilot.memory.journal import TradeJournal
from tradepilot.core.models import (
    TradeRecord, PendingProposal, TradeProposal, Action, OrderType,
    TimeInForce, RiskStatus, TradeLifecycleStatus, ExecutionStatus
)

client = TestClient(app)
FIXED_TS = datetime(2023, 10, 1, 12, 0, tzinfo=timezone.utc)

def _mock_trade_record(journal, symbol="PLTR", action=Action.BUY, status=ExecutionStatus.FILLED):
    tid = uuid.uuid4()
    record = TradeRecord(
        trade_id=tid,
        thesis_id=uuid.uuid4(),
        alpaca_order_id=f"alpaca-{tid}",
        symbol=symbol,
        action=action,
        quantity=10.0,
        filled_quantity=10.0,
        average_fill_price=25.0,
        original_thesis="Test",
        original_confidence=0.9,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=status,
        entry_price=25.0,
        entry_timestamp=FIXED_TS
    )
    journal.save_record(record)
    return tid, record

def _mock_pending_proposal(journal, symbol="TSLA", status=TradeLifecycleStatus.HUMAN_APPROVED):
    tid = uuid.uuid4()
    tp = TradeProposal(
        trade_id=tid, thesis_id=uuid.uuid4(), symbol=symbol, action=Action.BUY,
        order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY, quantity=5.0,
        current_price=200.0, confidence=0.8, reasoning="Test", assumptions=[],
        invalidation_conditions=[]
    )
    p = PendingProposal(
        trade_id=tid, thesis_id=tp.thesis_id, symbol=symbol, action=Action.BUY,
        quantity=tp.quantity, reference_price=tp.current_price, snapshot_timestamp=FIXED_TS,
        risk_decision=RiskStatus.APPROVED, risk_reasons=[], lifecycle_status=status,
        proposal_snapshot=tp, created_at=FIXED_TS
    )
    journal.save_pending_proposal(p)
    return tid, p

class TestJournalAPI:
    def test_api_trades_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        TradeJournal(db_path=db)
        with patch("tradepilot.api.DB_PATH", db):
            res = client.get("/api/trades")
        assert res.status_code == 200
        assert res.json()["trades"] == []

    def test_api_trades_returns_records(self, tmp_path):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        tid1, _ = _mock_trade_record(j, symbol="PLTR")
        tid2, _ = _mock_pending_proposal(j, symbol="TSLA")
        
        with patch("tradepilot.api.DB_PATH", db):
            res = client.get("/api/trades")
            
        assert res.status_code == 200
        trades = res.json()["trades"]
        assert len(trades) == 2
        
        symbols = [t["symbol"] for t in trades]
        assert "PLTR" in symbols
        assert "TSLA" in symbols
        
        # Verify historical PLTR attributes
        pltr = next(t for t in trades if t["symbol"] == "PLTR")
        assert pltr["lifecycle_status"] == "EXECUTED"
        assert pltr["execution_status"] == "FILLED"
        assert pltr["order_type"] == "UNKNOWN" # Historical defaults
        
        # Verify pending TSLA attributes
        tsla = next(t for t in trades if t["symbol"] == "TSLA")
        assert tsla["lifecycle_status"] == "HUMAN_APPROVED"
        assert tsla["execution_status"] is None
        assert tsla["order_type"] == "MARKET"

    def test_filtering(self, tmp_path):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        _mock_trade_record(j, symbol="PLTR", action=Action.BUY)
        _mock_trade_record(j, symbol="NVDA", action=Action.SELL)
        _mock_pending_proposal(j, symbol="AAPL")
        
        with patch("tradepilot.api.DB_PATH", db):
            res_sym = client.get("/api/trades?symbol=nvda")
            assert len(res_sym.json()["trades"]) == 1
            assert res_sym.json()["trades"][0]["symbol"] == "NVDA"
            
            res_act = client.get("/api/trades?action=sell")
            assert len(res_act.json()["trades"]) == 1
            assert res_act.json()["trades"][0]["symbol"] == "NVDA"

    def test_get_trade_details_historical(self, tmp_path):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        tid, _ = _mock_trade_record(j, symbol="PLTR")
        
        with patch("tradepilot.api.DB_PATH", db):
            res = client.get(f"/api/trades/{str(tid)}")
            
        assert res.status_code == 200
        d = res.json()
        assert d["symbol"] == "PLTR"
        assert d["lifecycle_status"] == "EXECUTED"
        assert d["execution_status"] == "FILLED"
        assert d["filled_quantity"] == 10.0
        assert d["has_decision_trace"] is True

    def test_get_trade_details_pending(self, tmp_path):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        tid, _ = _mock_pending_proposal(j, symbol="TSLA")
        
        with patch("tradepilot.api.DB_PATH", db):
            res = client.get(f"/api/trades/{str(tid)}")
            
        assert res.status_code == 200
        d = res.json()
        assert d["symbol"] == "TSLA"
        assert d["lifecycle_status"] == "HUMAN_APPROVED"
        assert d["execution_status"] is None
        assert d["filled_quantity"] == 0.0

    def test_unknown_trade_404(self, tmp_path):
        db = str(tmp_path / "test.db")
        TradeJournal(db_path=db)
        with patch("tradepilot.api.DB_PATH", db):
            assert client.get(f"/api/trades/{str(uuid.uuid4())}").status_code == 404

    def test_invalid_uuid_400(self):
        assert client.get("/api/trades/not-a-uuid").status_code == 400

    def test_data_integrity_no_mutations(self, tmp_path):
        db = str(tmp_path / "test.db")
        j = TradeJournal(db_path=db)
        tid, rec = _mock_trade_record(j, symbol="PLTR")
        
        with patch("tradepilot.api.DB_PATH", db):
            res = client.get("/api/trades")
        
        trades = res.json()["trades"]
        pltr = trades[0]
        
        # Verify EXACT preservation of metrics
        assert pltr["filled_quantity"] == rec.filled_quantity
        assert pltr["average_fill_price"] == rec.average_fill_price
        assert pltr["alpaca_order_id"] == rec.alpaca_order_id
        assert pltr["execution_status"] == rec.execution_status.value
