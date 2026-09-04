import pytest
import uuid
import os
from datetime import datetime, timezone
from tradepilot.core.models import TradeRecord, Action, ExecutionStatus, RiskStatus, TradeMemory, MarketSnapshot, PortfolioState, MemoryInfluence
from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore
from tradepilot.agents.llm_strategy import LLMStrategyAgent

@pytest.fixture
def journal():
    db_path = f"data/test_journal_{uuid.uuid4()}.db"
    return TradeJournal(db_path=db_path)

@pytest.fixture
def store():
    db_path = f"data/test_store_{uuid.uuid4()}.db"
    return MemoryStore(db_path=db_path)

def test_journal_save_and_retrieve(journal):
    record = TradeRecord(
        trade_id=uuid.uuid4(),
        thesis_id=uuid.uuid4(),
        symbol="AAPL",
        action=Action.BUY,
        quantity=10,
        original_thesis="Test",
        original_confidence=0.8,
        assumptions=["A"],
        invalidation_conditions=["B"],
        risk_decision=RiskStatus.APPROVED,
        entry_price=150.0,
        entry_timestamp=datetime.now(timezone.utc)
    )
    
    journal.save_record(record)
    retrieved = journal.get_record(record.trade_id)
    
    assert retrieved is not None
    assert retrieved.trade_id == record.trade_id
    assert retrieved.symbol == "AAPL"
    assert retrieved.entry_price == 150.0

def test_trade_ids_remain_unique(journal):
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    assert id1 != id2

def test_store_save_and_retrieve_prioritization(store):
    trade_id = uuid.uuid4()
    
    mem1 = TradeMemory(
        source_trade_id=trade_id,
        symbol="AAPL",
        market_condition="Bull",
        lesson="Buy dip",
        applicable_conditions=["Bull"],
        evidence_count=1,
        confidence=0.5
    )
    mem2 = TradeMemory(
        source_trade_id=trade_id,
        symbol="AAPL",
        market_condition="Bull",
        lesson="Wait",
        applicable_conditions=["Bull"],
        evidence_count=5,
        confidence=0.9
    )
    mem3 = TradeMemory(
        source_trade_id=trade_id,
        symbol="MSFT",
        market_condition="Bull",
        lesson="Buy",
        applicable_conditions=["Bull"],
        evidence_count=10,
        confidence=0.9
    )
    
    store.save_memory(mem1)
    store.save_memory(mem2)
    store.save_memory(mem3)
    
    # Retrieve should prioritize same symbol, then evidence_count, then confidence
    retrieved = store.retrieve_memories("AAPL", limit=2)
    assert len(retrieved) == 2
    assert retrieved[0].lesson == "Wait" # mem2 (higher evidence and confidence)
    assert retrieved[1].lesson == "Buy dip" # mem1
