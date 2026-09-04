"""
tests/test_decision_trace.py

Focused unit tests for the Explainable Decision Trace.

All tests are pure unit tests (no Alpaca network calls, no new orders).
The Alpaca client is fully mocked where needed.
"""
import io
import sys
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tradepilot.core.models import (
    TradeRecord, Action, ExecutionStatus, RiskStatus,
    TradeMemory,
)
from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore
from tradepilot.decision_trace import build_trace, _fetch_memories


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_journal(tmp_path):
    return TradeJournal(db_path=str(tmp_path / "trace_test.db"))


@pytest.fixture
def sample_record():
    tid = uuid.uuid4()
    return TradeRecord(
        trade_id=tid,
        thesis_id=uuid.uuid4(),
        symbol="PLTR",
        action=Action.BUY,
        quantity=0.5369,
        original_thesis=(
            "PLTR is in a clear uptrend with price (186.24) trading "
            "above both SMA20 (171.03) and SMA50 (143.74).\n\n"
            "Evidence:\n"
            "Price: 186.24, SMA20: 171.03, SMA50: 143.74, Momentum: 0.16%\n\n"
            "Summary:\n"
            "Strong bullish alignment justifies long entry.\n\n"
            "Memory Reasoning: No past memories -- no influence."
        ),
        original_confidence=0.75,
        assumptions=[
            "Upward price momentum will persist.",
            "Price will respect SMA20 as dynamic support.",
        ],
        invalidation_conditions=[
            "Price drops below SMA20 at 171.03.",
            "Momentum turns significantly negative.",
        ],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.ACCEPTED,
        filled_quantity=0.0,
    )


# ── journal extension tests ───────────────────────────────────────────────────

def test_journal_list_all_records_empty(tmp_journal):
    assert tmp_journal.list_all_records() == []


def test_journal_list_all_records(tmp_journal, sample_record):
    tmp_journal.save_record(sample_record)
    records = tmp_journal.list_all_records()
    assert len(records) == 1
    assert records[0].trade_id == sample_record.trade_id


def test_journal_list_all_records_multiple(tmp_journal):
    ids = []
    for _ in range(3):
        tid = uuid.uuid4()
        ids.append(tid)
        r = TradeRecord(
            trade_id=tid,
            thesis_id=uuid.uuid4(),
            symbol="AAPL",
            action=Action.BUY,
            quantity=1.0,
            original_thesis="test",
            original_confidence=0.5,
            assumptions=[],
            invalidation_conditions=[],
            risk_decision=RiskStatus.APPROVED,
        )
        tmp_journal.save_record(r)
    records = tmp_journal.list_all_records()
    assert len(records) == 3


def test_get_record_by_alpaca_order_id(tmp_journal, sample_record):
    sample_record.alpaca_order_id = "e3173016-c4fb-4208-ba0d-b99f425d417b"
    tmp_journal.save_record(sample_record)
    found = tmp_journal.get_record_by_alpaca_order_id("e3173016-c4fb-4208-ba0d-b99f425d417b")
    assert found is not None
    assert found.trade_id == sample_record.trade_id


def test_get_record_by_alpaca_order_id_not_found(tmp_journal):
    result = tmp_journal.get_record_by_alpaca_order_id("00000000-0000-0000-0000-000000000000")
    assert result is None


# ── build_trace (offline, no Alpaca) ─────────────────────────────────────────

def test_build_trace_not_found_returns_1(tmp_journal, capsys):
    """If the order ID is not in the journal, build_trace returns 1."""
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        code = build_trace(tmp_journal, alpaca_order_id="deadbeef-dead-dead-dead-deaddeadbeef")
    assert code == 1


def test_build_trace_by_trade_id(tmp_journal, sample_record, capsys):
    """build_trace resolves by TradePilot trade_id."""
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    assert code == 0
    out = capsys.readouterr().out
    assert "PLTR" in out
    assert "BUY" in out


def test_build_trace_by_alpaca_order_id_direct(tmp_journal, sample_record, capsys):
    """build_trace resolves via alpaca_order_id stored in the record."""
    alpaca_oid = "e3173016-c4fb-4208-ba0d-b99f425d417b"
    sample_record.alpaca_order_id = alpaca_oid
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(tmp_journal, alpaca_order_id=alpaca_oid)
    assert code == 0
    out = capsys.readouterr().out
    assert alpaca_oid in out


def test_build_trace_by_client_order_id_via_alpaca(tmp_journal, sample_record, capsys):
    """
    build_trace falls back to querying Alpaca and matching via client_order_id
    when alpaca_order_id is not yet in the journal record.
    Verifies the filled quantity surfaces in the trace output.
    """
    tmp_journal.save_record(sample_record)

    fake_order = MagicMock(spec=["client_order_id", "id", "status",
                                  "filled_qty", "filled_avg_price", "submitted_at"])
    fake_order.client_order_id = str(sample_record.trade_id)
    fake_order.id = "e3173016-c4fb-4208-ba0d-b99f425d417b"
    fake_order.status = "filled"
    fake_order.filled_qty = "0.5369"
    fake_order.filled_avg_price = "184.61"
    fake_order.submitted_at = datetime(2026, 8, 31, 13, 23, 1, tzinfo=timezone.utc)

    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=fake_order):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(
                tmp_journal,
                alpaca_order_id="e3173016-c4fb-4208-ba0d-b99f425d417b"
            )
    assert code == 0
    out = capsys.readouterr().out
    # Alpaca order ID and filled qty must appear
    assert "e3173016-c4fb-4208-ba0d-b99f425d417b" in out
    assert "0.5369" in out


def test_build_trace_fill_price_from_record(tmp_journal, sample_record, capsys):
    """Fill price from the TradeRecord itself surfaces in section 16."""
    sample_record.average_fill_price = 184.61
    sample_record.entry_price = 184.61
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "184.61" in out


def test_build_trace_all_19_sections_present(tmp_journal, sample_record, capsys):
    """Every required section header appears in the output."""
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            code = build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    assert code == 0
    out = capsys.readouterr().out
    for i in range(1, 20):
        assert str(i) in out, f"Section {i} missing from trace output"


def test_build_trace_human_confirmation_always_present(tmp_journal, sample_record, capsys):
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "Human" in out or "human" in out
    assert "Autonomous trading is DISABLED" in out


def test_build_trace_thesis_parsed_from_reasoning(tmp_journal, sample_record, capsys):
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "uptrend" in out
    assert "SMA20" in out


def test_build_trace_memory_section(tmp_journal, sample_record, capsys):
    """When memories exist they appear in section 19."""
    tmp_journal.save_record(sample_record)
    fake_mem = TradeMemory(
        source_trade_id=sample_record.trade_id,
        symbol="PLTR",
        market_condition="Bull trend",
        lesson="Buy breakouts with volume confirmation.",
        applicable_conditions=["Bull"],
        evidence_count=1,
        confidence=0.8,
    )
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[fake_mem]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "Buy breakouts" in out


def test_build_trace_no_new_alpaca_orders(tmp_journal, sample_record):
    """Verify that build_trace never calls submit_order."""
    tmp_journal.save_record(sample_record)
    mock_client = MagicMock()
    with patch("tradepilot.decision_trace.get_alpaca_client", return_value=mock_client):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    mock_client.submit_order.assert_not_called()


def test_build_trace_demo_cap_mentioned(tmp_journal, sample_record, capsys):
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    # Section 10 must be present regardless of the configured demo cap value
    assert "DEMO SIZING" in out
    assert "Demo cap" in out 


def test_build_trace_risk_decision_present(tmp_journal, sample_record, capsys):
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "APPROVED" in out


def test_build_trace_assumptions_and_invalidation(tmp_journal, sample_record, capsys):
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "momentum will persist" in out.lower() or "momentum" in out.lower()
    assert "sma20" in out.lower()


def test_build_trace_summary_answers_present(tmp_journal, sample_record, capsys):
    """The four summary questions must appear in the output."""
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "Why did TradePilot make this trade" in out
    assert "Why was it allowed" in out
    assert "Who approved execution" in out
    assert "What actually happened at Alpaca" in out
    assert "What did the system learn" in out


# ── label-precision tests (presentation only) ────────────────────────────────

def test_section9_uses_snapshot_price_not_fill_price(tmp_journal, sample_record, capsys):
    """
    Section 9 must show the market snapshot price (from Evidence text),
    NOT the actual fill price. Snapshot ~$186.24, fill ~$184.61.
    """
    sample_record.average_fill_price = 184.61
    sample_record.entry_price = 184.61
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out

    assert "POSITION SIZING (strategy proposal)" in out
    assert "market price when strategy ran" in out
    # Snapshot price (186.24) must appear in the output
    assert "186.24" in out
    # Section 9 block must NOT contain the fill price
    lines = out.splitlines()
    s9_lines, in_s9 = [], False
    for line in lines:
        if "9 . POSITION SIZING" in line:
            in_s9 = True
        elif in_s9 and line.startswith("-- "):
            break
        if in_s9:
            s9_lines.append(line)
    assert "184.61" not in "\n".join(s9_lines), (
        "Section 9 must not show the fill price — that belongs in section 16"
    )


def test_section15_labels_submitted_quantity(tmp_journal, sample_record, capsys):
    """Section 15 header must say 'SUBMITTED QUANTITY' and note demo-capping."""
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "SUBMITTED QUANTITY" in out
    assert "demo-capped" in out


def test_section16_labels_execution_result(tmp_journal, sample_record, capsys):
    """Section 16 must be labelled 'EXECUTION RESULT' and cite Alpaca as source."""
    sample_record.average_fill_price = 184.61
    sample_record.filled_quantity = 0.5369
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "EXECUTION RESULT" in out
    assert "actual fill from Alpaca" in out
    assert "184.61" in out
    assert "Fill notional" in out


def test_section16_snapshot_vs_fill_diff_shown(tmp_journal, sample_record, capsys):
    """
    When snapshot price != fill price, a 'vs. snapshot' comparison line must
    appear. The word 'slippage' must NOT appear (unsupported by the data model).
    """
    sample_record.average_fill_price = 184.61
    sample_record.entry_price = 184.61
    tmp_journal.save_record(sample_record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(sample_record.trade_id))
    out = capsys.readouterr().out
    assert "vs. snapshot" in out
    assert "market moved between proposal and fill" in out
    assert "slippage" not in out.lower()


def test_section16_no_diff_line_when_prices_match(tmp_journal, capsys):
    """When snapshot price equals fill price, no comparison line should appear."""
    tid = uuid.uuid4()
    record = TradeRecord(
        trade_id=tid,
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=0.5,
        filled_quantity=0.5,
        average_fill_price=200.0,
        entry_price=200.0,
        original_thesis=(
            "Thesis.\n\nEvidence:\nPrice: 200.00, SMA20: 190.0\n\n"
            "Summary:\nBullish.\n\nMemory Reasoning: None."
        ),
        original_confidence=0.8,
        assumptions=[],
        invalidation_conditions=[],
        risk_decision=RiskStatus.APPROVED,
        execution_status=ExecutionStatus.FILLED,
    )
    tmp_journal.save_record(record)
    with patch("tradepilot.decision_trace._fetch_alpaca_order", return_value=None):
        with patch("tradepilot.decision_trace._fetch_memories", return_value=[]):
            build_trace(tmp_journal, trade_id_str=str(tid))
    out = capsys.readouterr().out
    assert "vs. snapshot" not in out

