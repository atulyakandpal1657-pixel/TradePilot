import uuid
from io import StringIO
import pytest
from tradepilot.core.models import TradeRecord, Action, ExecutionStatus
from tradepilot.memory.journal import TradeJournal
from tradepilot.trade_status import display_trade_status

@pytest.fixture
def temp_journal(tmp_path):
    db_path = str(tmp_path / "test_tradepilot.db")
    journal = TradeJournal(db_path=db_path)
    return journal, db_path

def test_trade_status_valid_record(temp_journal, capsys):
    journal, db_path = temp_journal
    
    trade_id = uuid.uuid4()
    thesis_id = uuid.uuid4()
    
    record = TradeRecord(
        trade_id=trade_id,
        thesis_id=thesis_id,
        symbol="AAPL",
        action=Action.BUY,
        quantity=1.5,
        filled_quantity=1.5,
        average_fill_price=150.0,
        alpaca_order_id="fake-alpaca-id",
        execution_status=ExecutionStatus.FILLED,
        original_thesis="Test thesis",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision="APPROVED"
    )
    journal.save_record(record)
    
    code = display_trade_status(str(trade_id), db_path=db_path)
    assert code == 0
    
    out, err = capsys.readouterr()
    assert str(trade_id) in out
    assert str(thesis_id) in out
    assert "AAPL" in out
    assert "BUY" in out
    assert "1.5" in out
    assert "$150.00" in out
    assert "FILLED" in out
    assert "fake-alpaca-id" in out

def test_trade_status_invalid_uuid(temp_journal, capsys):
    journal, db_path = temp_journal
    code = display_trade_status("not-a-uuid", db_path=db_path)
    assert code == 1
    
    out, err = capsys.readouterr()
    assert "not a valid UUID" in err

def test_trade_status_not_found(temp_journal, capsys):
    journal, db_path = temp_journal
    trade_id = uuid.uuid4()
    code = display_trade_status(str(trade_id), db_path=db_path)
    assert code == 1
    
    out, err = capsys.readouterr()
    assert "not found" in err
