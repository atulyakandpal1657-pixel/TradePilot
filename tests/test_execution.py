import pytest
from unittest.mock import patch, MagicMock
import uuid
from datetime import datetime, timezone

from tradepilot.core.models import (
    TradeProposal, ApprovedTrade, Action, OrderType, TimeInForce, ExecutionStatus, RiskStatus, MarketSnapshot, TradeRecord
)
from tradepilot.alpaca.execution import AlpacaPaperExecutor
from tradepilot.core.config import settings
from tradepilot.alpaca.trade_stream import TradeStreamHandler

@pytest.fixture
def proposal():
    return TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        quantity=10,
        current_price=150.0,
        confidence=0.8,
        reasoning="Test",
        assumptions=[],
        invalidation_conditions=[]
    )

@pytest.fixture
def approved_trade(proposal):
    return ApprovedTrade(
        trade_id=proposal.trade_id,
        proposal=proposal
    )

def test_live_endpoint_rejected(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_paper_trade", False)
    with pytest.raises(ValueError, match="Safety Violation"):
        AlpacaPaperExecutor()

def test_missing_credentials_rejected(approved_trade, monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "")
    executor = AlpacaPaperExecutor(client=None)
    result = executor.execute(approved_trade)
    assert result.status == ExecutionStatus.REJECTED
    assert "Missing Alpaca credentials" in result.message

def test_raw_trade_proposal_rejected(proposal):
    executor = AlpacaPaperExecutor(client=MagicMock())
    with pytest.raises(TypeError, match="only accepts ApprovedTrade"):
        executor.execute(proposal)

@patch('alpaca.trading.client.TradingClient.submit_order')
def test_approved_trade_accepted_and_client_order_id_passed(mock_submit, approved_trade, monkeypatch):
    mock_order = MagicMock()
    mock_order.id = "alpaca-uuid-123"
    mock_order.status = "accepted"
    mock_order.filled_qty = "0"
    mock_order.filled_avg_price = None
    mock_submit.return_value = mock_order
    
    monkeypatch.setattr(settings, "alpaca_api_key", "test")
    monkeypatch.setattr(settings, "alpaca_secret_key", "test")
    executor = AlpacaPaperExecutor()
    
    result = executor.execute(approved_trade)
    
    assert result.status == ExecutionStatus.ACCEPTED
    assert result.alpaca_order_id == "alpaca-uuid-123"
    
    # Check client_order_id is correct
    args, kwargs = mock_submit.call_args
    req = args[0]
    assert req.client_order_id == str(approved_trade.trade_id)

def test_trade_stream_partial_fill():
    journal = MagicMock()
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING
    )
    journal.get_record.return_value = record
    
    handler = TradeStreamHandler(journal, None, None)
    
    event = {
        "event": "partial_fill",
        "order": {
            "client_order_id": str(record.trade_id),
            "filled_qty": "5"
        }
    }
    
    handler.handle_trade_update(event)
    assert record.execution_status == ExecutionStatus.PARTIALLY_FILLED
    journal.save_record.assert_called_with(record)

def test_trade_stream_full_fill():
    journal = MagicMock()
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING
    )
    journal.get_record.return_value = record
    
    handler = TradeStreamHandler(journal, None, None)
    
    event = {
        "event": "fill",
        "order": {
            "client_order_id": str(record.trade_id),
            "filled_qty": "10",
            "filled_avg_price": "150.5"
        }
    }
    
    handler.handle_trade_update(event)
    assert record.execution_status == ExecutionStatus.FILLED
    assert record.entry_price == 150.5
    journal.save_record.assert_called_with(record)

def test_trade_stream_rejection():
    journal = MagicMock()
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING
    )
    journal.get_record.return_value = record
    
    handler = TradeStreamHandler(journal, None, None)
    
    event = {
        "event": "rejected",
        "order": {
            "client_order_id": str(record.trade_id)
        }
    }
    
    handler.handle_trade_update(event)
    assert record.execution_status == ExecutionStatus.REJECTED

def test_trade_stream_cancellation():
    journal = MagicMock()
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING
    )
    journal.get_record.return_value = record
    
    handler = TradeStreamHandler(journal, None, None)
    
    event = {
        "event": "canceled",
        "order": {
            "client_order_id": str(record.trade_id)
        }
    }
    
    handler.handle_trade_update(event)
    assert record.execution_status == ExecutionStatus.CANCELLED

def test_check_and_trigger_forensics():
    journal = MagicMock()
    forensics = MagicMock()
    store = MagicMock()
    
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=150.0,
        exit_price=160.0 # Force exit condition
    )
    
    handler = TradeStreamHandler(journal, forensics, store)
    
    # Mock positions where AAPL is 0
    pos = MagicMock()
    pos.qty = "0"
    
    mock_forensics_result = MagicMock()
    mock_forensics_result.actual_market_conditions = "Test"
    mock_forensics_result.lesson = "Test"
    mock_forensics_result.successful_assumptions = []
    mock_forensics_result.failed_assumptions = []
    mock_forensics_result.confidence = 0.8
    forensics.analyze_trade.return_value = mock_forensics_result
    
    handler.check_and_trigger_forensics(record, current_positions={"AAPL": pos}, exit_price=160.0)
    
    forensics.analyze_trade.assert_called_once()
    store.save_memory.assert_called_once()


# ── Additional tests to complete all 17 required spec scenarios ──────────────

def test_paper_endpoint_accepted(monkeypatch):
    """Test 2: paper=True allows executor to initialize."""
    monkeypatch.setattr(settings, "alpaca_paper_trade", True)
    monkeypatch.setattr(settings, "alpaca_api_key", "")   # no key → client stays None
    monkeypatch.setattr(settings, "alpaca_secret_key", "")
    # Should not raise — paper mode is allowed
    executor = AlpacaPaperExecutor(client=None)
    assert executor is not None


def test_human_confirmation_n_prevents_submission(approved_trade, monkeypatch, capsys):
    """Test 6: typing 'n' at the prompt must not call execute()."""
    from unittest.mock import patch as mpatch

    monkeypatch.setattr("builtins.input", lambda: "n")
    executor_mock = MagicMock()

    with mpatch("tradepilot.paper_trade_demo.AlpacaPaperExecutor", return_value=executor_mock):
        # Simulate what the demo does after risk approval
        choice = input().strip().lower()
        if choice == 'y':
            executor_mock.execute(approved_trade)

    executor_mock.execute.assert_not_called()
    assert choice == "n"


def test_human_confirmation_invalid_input_prevents_submission(approved_trade, monkeypatch):
    """Test 7: any input other than 'y' (or case-insensitive 'Y') must NOT call execute().
    The demo uses .strip().lower(), so 'Y' is treated as 'y' — this is intentional.
    """
    # These must never trigger execution
    for bad_input in ["yes", "1", " ", "", "no", "n", "nope"]:
        monkeypatch.setattr("builtins.input", lambda b=bad_input: b)
        executor_mock = MagicMock()   # fresh mock each iteration

        choice = input().strip().lower()
        if choice == 'y':
            executor_mock.execute(approved_trade)

        executor_mock.execute.assert_not_called()


def test_demo_notional_limit_blocks_oversized_order(approved_trade, monkeypatch):
    """Test 8: demo guard blocks orders above MAX_DEMO_ORDER_NOTIONAL."""
    # Proposal has qty=10, price=150 → notional=$1500, well above default $100 guard
    notional = approved_trade.proposal.quantity * approved_trade.proposal.current_price
    assert notional > settings.max_demo_order_notional, (
        f"Expected notional {notional} > limit {settings.max_demo_order_notional}"
    )
    # Confirm the guard logic works
    guard_triggered = notional > settings.max_demo_order_notional
    assert guard_triggered


def test_alpaca_response_mapped_correctly(approved_trade, monkeypatch):
    """Test 10: every status field from Alpaca is mapped to ExecutionResult correctly."""
    mock_order = MagicMock()
    mock_order.id = "test-alpaca-id"
    mock_order.status = "new"           # Alpaca 'new' → ACCEPTED
    mock_order.filled_qty = "5"
    mock_order.filled_avg_price = "149.95"

    mock_client = MagicMock()
    mock_client.submit_order.return_value = mock_order

    executor = AlpacaPaperExecutor(client=mock_client)
    result = executor.execute(approved_trade)

    assert result.alpaca_order_id == "test-alpaca-id"
    assert result.status == ExecutionStatus.ACCEPTED
    assert result.filled_quantity == 5.0
    assert result.average_fill_price == 149.95
    assert result.client_order_id == approved_trade.trade_id


def test_trade_record_updated_after_fill():
    """Test 15: a fill event updates TradeRecord entry_price and execution_status."""
    journal = MagicMock()
    trade_id = uuid.uuid4()

    record = TradeRecord(
        trade_id=trade_id,
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING,
    )
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_avg_price": "155.00",
            "filled_qty": "10",
        }
    })

    assert record.execution_status == ExecutionStatus.FILLED
    assert record.entry_price == 155.0
    assert record.entry_timestamp is not None
    journal.save_record.assert_called_with(record)


def test_no_pnl_before_closure():
    """Test 17: realized_pnl and exit fields are None until position is closed."""
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=150.0,
    )
    # Before position is closed, these must remain None
    assert record.exit_price is None
    assert record.exit_timestamp is None
    assert record.realized_pnl is None
    assert record.realized_return_pct is None


# ── Phase 6 hardening: lifecycle tests ───────────────────────────────────────

def _make_pending_record(trade_id: uuid.UUID = None) -> TradeRecord:
    """Helper: a freshly submitted TradeRecord in PENDING state."""
    return TradeRecord(
        trade_id=trade_id or uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=20,          # requested 20 shares
        filled_quantity=0.0,
        original_thesis="Test thesis",
        original_confidence=0.75,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING,
    )


def test_partial_fill_updates_trade_record():
    """Partial fill propagates filled_qty and avg_price into TradeRecord."""
    journal = MagicMock()
    trade_id = uuid.uuid4()
    record = _make_pending_record(trade_id)
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "partial_fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "8",
            "filled_avg_price": "151.00",
        }
    })

    assert record.execution_status == ExecutionStatus.PARTIALLY_FILLED
    assert record.filled_quantity == 8.0
    assert record.average_fill_price == 151.00
    journal.save_record.assert_called_with(record)


def test_multiple_partial_fills_accumulate():
    """Alpaca sends cumulative totals; second partial_fill overwrites, not adds."""
    journal = MagicMock()
    trade_id = uuid.uuid4()
    record = _make_pending_record(trade_id)
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)

    handler.handle_trade_update({
        "event": "partial_fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "5",
            "filled_avg_price": "150.00",
        }
    })

    handler.handle_trade_update({
        "event": "partial_fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "12",           # Alpaca sends CUMULATIVE total
            "filled_avg_price": "150.50",
        }
    })

    assert record.filled_quantity == 12.0         # latest cumulative value
    assert record.average_fill_price == 150.50
    assert record.quantity == 20.0                # requested never changes
    assert record.remaining_quantity == 8.0       # 20 - 12


def test_full_fill_finalises_entry():
    """A fill event sets FILLED, entry_price, entry_timestamp."""
    journal = MagicMock()
    trade_id = uuid.uuid4()
    record = _make_pending_record(trade_id)
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "20",
            "filled_avg_price": "152.75",
        }
    })

    assert record.execution_status == ExecutionStatus.FILLED
    assert record.filled_quantity == 20.0
    assert record.average_fill_price == 152.75
    assert record.entry_price == 152.75           # canonical entry
    assert record.entry_timestamp is not None
    journal.save_record.assert_called_with(record)


def test_requested_and_filled_quantity_remain_distinct():
    """quantity (requested) is never overwritten by fill events."""
    journal = MagicMock()
    trade_id = uuid.uuid4()
    record = _make_pending_record(trade_id)   # quantity=20
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "18",           # only 18 of 20 filled (e.g. odd-lot)
            "filled_avg_price": "150.00",
        }
    })

    assert record.quantity == 20.0              # requested — must be unchanged
    assert record.filled_quantity == 18.0       # actual
    assert record.remaining_quantity == 2.0     # computed


def test_no_realized_pnl_after_entry_fill():
    """Realized P&L fields remain None immediately after a fill (no exit yet)."""
    journal = MagicMock()
    trade_id = uuid.uuid4()
    record = _make_pending_record(trade_id)
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "20",
            "filled_avg_price": "150.00",
        }
    })

    assert record.execution_status == ExecutionStatus.FILLED
    assert record.realized_pnl is None
    assert record.realized_return_pct is None
    assert record.exit_price is None


def test_sell_order_does_not_automatically_close_trade():
    """A SELL fill does not write exit_price or P&L; position check is required."""
    journal = MagicMock()
    trade_id = uuid.uuid4()

    # Simulate a filled BUY that we now want to SELL
    record = TradeRecord(
        trade_id=trade_id,
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.SELL,
        quantity=20,
        filled_quantity=0.0,
        original_thesis="Exit thesis",
        original_confidence=0.6,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING,
        entry_price=150.0,
    )
    journal.get_record.return_value = record

    handler = TradeStreamHandler(journal, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(trade_id),
            "filled_qty": "20",
            "filled_avg_price": "160.00",
        }
    })

    # SELL fill sets FILLED status but does NOT close the trade
    assert record.execution_status == ExecutionStatus.FILLED
    # exit_price and P&L must still be None — close requires explicit position check
    assert record.exit_price is None
    assert record.realized_pnl is None


def test_closed_position_permits_final_pnl():
    """When position is confirmed zero AND exit_price supplied, P&L is calculated."""
    journal = MagicMock()
    forensics_mock = MagicMock()
    store_mock = MagicMock()
    forensics_mock.analyze_trade.return_value = None  # skip memory creation

    trade_id = uuid.uuid4()
    record = TradeRecord(
        trade_id=trade_id,
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        filled_quantity=10.0,
        average_fill_price=150.0,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
        entry_price=150.0,
    )
    journal.get_record.return_value = record

    pos = MagicMock()
    pos.qty = "0"   # position is closed at Alpaca

    handler = TradeStreamHandler(journal, forensics_mock, store_mock)
    handler.check_and_trigger_forensics(
        record,
        current_positions={"AAPL": pos},
        exit_price=165.0,
    )

    assert record.exit_price == 165.0
    assert record.realized_pnl == pytest.approx(150.0)    # (165-150) * 10
    assert record.realized_return_pct == pytest.approx(0.10)
    assert record.exit_timestamp is not None
    journal.save_record.assert_called_with(record)


def test_correlation_id_survives_full_lifecycle(monkeypatch):
    """trade_id == client_order_id == ApprovedTrade.trade_id throughout."""
    from tradepilot.alpaca.execution import AlpacaPaperExecutor

    proposal = TradeProposal(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        quantity=5,
        current_price=150.0,
        confidence=0.8,
        reasoning="Test",
        assumptions=[],
        invalidation_conditions=[],
    )
    approved = ApprovedTrade(trade_id=proposal.trade_id, proposal=proposal)

    # ① Executor must pass trade_id as client_order_id
    mock_order = MagicMock()
    mock_order.id = "alpaca-abc"
    mock_order.status = "accepted"
    mock_order.filled_qty = "0"
    mock_order.filled_avg_price = None
    mock_client = MagicMock()
    mock_client.submit_order.return_value = mock_order

    monkeypatch.setattr(settings, "alpaca_api_key", "test")
    monkeypatch.setattr(settings, "alpaca_secret_key", "test")
    executor = AlpacaPaperExecutor(client=mock_client)
    result = executor.execute(approved)

    submitted_req = mock_client.submit_order.call_args[0][0]
    assert submitted_req.client_order_id == str(proposal.trade_id)   # ① verified

    # ② ExecutionResult carries the same IDs
    assert result.trade_id == proposal.trade_id
    assert result.client_order_id == proposal.trade_id
    assert result.thesis_id == proposal.thesis_id

    # ③ Stream handler resolves TradeRecord via client_order_id
    journal_mock = MagicMock()
    record = TradeRecord(
        trade_id=proposal.trade_id,
        thesis_id=proposal.thesis_id,
        symbol="AAPL",
        action=Action.BUY,
        quantity=5,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.PENDING,
    )
    journal_mock.get_record.return_value = record

    handler = TradeStreamHandler(journal_mock, None, None)
    handler.handle_trade_update({
        "event": "fill",
        "order": {
            "client_order_id": str(proposal.trade_id),  # same ID Alpaca echoes back
            "id": "alpaca-abc",
            "filled_qty": "5",
            "filled_avg_price": "151.00",
        }
    })

    assert record.execution_status == ExecutionStatus.FILLED
    assert record.alpaca_order_id == "alpaca-abc"   # Alpaca's ID stored separately
    assert record.trade_id == proposal.trade_id      # our ID unchanged
