"""
tests/test_trace_status_consistency.py

Regression tests for the identifier-lookup consistency between
trade_status and decision_trace.

These tests guarantee that:
  - trade_status finds a TradeRecord by trade_id
  - decision_trace finds the SAME TradeRecord by trade_id (via its
    alpaca_order_id parameter, which now also tries trade_id as a
    fallback before declaring "not found")
  - no false "TradeRecord not found" error is emitted
  - open trades (no exit_price) never show fabricated P&L or forensics

All tests are fully offline: no Alpaca orders are submitted or read.
"""

import uuid
from unittest.mock import patch

import pytest

from tradepilot.core.models import Action, ExecutionStatus, RiskStatus, TradeRecord
from tradepilot.decision_trace import build_trace
from tradepilot.memory.journal import TradeJournal
from tradepilot.trade_status import display_trade_status


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TRADE_ID = uuid.UUID("b6e16912-ee7e-47c6-a51b-a663064adaab")
ALPACA_OID = "e3173016-c4fb-4208-ba0d-b99f425d417b"
FILLED_QTY = 0.5369
FILL_PRICE = 184.61


@pytest.fixture()
def journal_with_pltr(tmp_path):
    """In-memory-like journal (tmp SQLite) pre-populated with a FILLED PLTR record."""
    db_path = str(tmp_path / "test.db")
    journal = TradeJournal(db_path=db_path)

    record = TradeRecord(
        trade_id=TRADE_ID,
        thesis_id=uuid.uuid4(),
        alpaca_order_id=ALPACA_OID,
        symbol="PLTR",
        action=Action.BUY,
        quantity=FILLED_QTY,
        filled_quantity=FILLED_QTY,
        average_fill_price=FILL_PRICE,
        entry_price=FILL_PRICE,
        original_thesis=(
            "PLTR thesis\n\nEvidence:\nPrice: 186.24\n\nSummary:\nBullish."
            "\n\nMemory Reasoning: No past memories."
        ),
        original_confidence=0.75,
        assumptions=["Momentum persists."],
        invalidation_conditions=["Price drops below SMA20 at 171.03."],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
    )
    journal.save_record(record)
    return journal, db_path


# ---------------------------------------------------------------------------
# trade_status tests
# ---------------------------------------------------------------------------


def test_trade_status_finds_pltr_by_trade_id(journal_with_pltr, capsys):
    """trade_status finds the PLTR record using its trade_id."""
    journal, db_path = journal_with_pltr
    code = display_trade_status(str(TRADE_ID), db_path=db_path)
    assert code == 0

    out, err = capsys.readouterr()
    assert str(TRADE_ID) in out
    assert "PLTR" in out
    assert "0.5369" in out
    assert "184.61" in out
    assert "FILLED" in out
    assert ALPACA_OID in out
    # No error output
    assert err == ""


def test_trade_status_open_trade_no_pnl(journal_with_pltr, capsys):
    """An open (unfilled exit) trade must not display fabricated P&L."""
    journal, db_path = journal_with_pltr
    display_trade_status(str(TRADE_ID), db_path=db_path)
    out, _ = capsys.readouterr()
    # exit_price is None on the fixture, so P&L line must be absent
    assert "Realized P&L" not in out


# ---------------------------------------------------------------------------
# decision_trace tests
# ---------------------------------------------------------------------------


def test_decision_trace_finds_pltr_by_trade_id(journal_with_pltr, capsys):
    """
    decision_trace must find the PLTR record when the user supplies the
    trade_id (= client_order_id), even though the parameter is named
    alpaca_order_id.  No 'ERROR: No TradeRecord found' must appear.
    """
    journal, db_path = journal_with_pltr

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(journal, alpaca_order_id=str(TRADE_ID))

    assert code == 0, "build_trace must return 0 when the record is found"
    out, err = capsys.readouterr()
    assert "ERROR" not in err, f"Unexpected stderr: {err!r}"
    assert str(TRADE_ID) in out
    assert "PLTR" in out


def test_decision_trace_finds_pltr_by_alpaca_order_id(journal_with_pltr, capsys):
    """decision_trace also resolves via the true alpaca_order_id."""
    journal, db_path = journal_with_pltr

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(journal, alpaca_order_id=ALPACA_OID)

    assert code == 0
    out, err = capsys.readouterr()
    assert "ERROR" not in err
    assert "PLTR" in out


def test_decision_trace_same_record_as_trade_status(journal_with_pltr, capsys):
    """
    Both commands must resolve to the same canonical trade_id.
    """
    journal, db_path = journal_with_pltr

    # trade_status output
    display_trade_status(str(TRADE_ID), db_path=db_path)
    status_out, _ = capsys.readouterr()

    # decision_trace output
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(journal, alpaca_order_id=str(TRADE_ID))
    trace_out, trace_err = capsys.readouterr()

    assert "ERROR" not in trace_err
    # Both show the same trade_id
    assert str(TRADE_ID) in status_out
    assert str(TRADE_ID) in trace_out
    # Both show the same fill data
    assert "0.5369" in status_out and "0.5369" in trace_out
    assert "184.61" in status_out and "184.61" in trace_out


def test_decision_trace_no_fabricated_pnl_for_open_trade(journal_with_pltr, capsys):
    """
    An open trade (no exit_price) must never show P&L figures in the trace.
    """
    journal, db_path = journal_with_pltr

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(journal, alpaca_order_id=str(TRADE_ID))

    out, _ = capsys.readouterr()
    assert "OPEN" in out                         # position is open
    assert "not yet closed" in out               # honest about no exit
    assert "realized_pnl" not in out.lower()     # no raw field names leaked
    assert "P&L" not in out or "forensics" in out  # P&L only in forensics section if at all


def test_decision_trace_no_fabricated_forensics_for_open_trade(journal_with_pltr, capsys):
    """Forensics must report 'pending', not fabricated results, for an open trade."""
    journal, db_path = journal_with_pltr

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(journal, alpaca_order_id=str(TRADE_ID))

    out, _ = capsys.readouterr()
    assert "forensics will run after exit" in out.lower()


def test_decision_trace_not_found_returns_error(tmp_path, capsys):
    """A genuinely unknown ID must still return exit code 1 and print the error."""
    db_path = str(tmp_path / "empty.db")
    journal = TradeJournal(db_path=db_path)
    unknown_id = str(uuid.uuid4())

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        code = build_trace(journal, alpaca_order_id=unknown_id)

    assert code == 1
    _, err = capsys.readouterr()
    assert "ERROR" in err
    assert "No TradeRecord found" in err
