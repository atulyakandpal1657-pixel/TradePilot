import uuid
import datetime
from tradepilot.core.models import (
    TradeRecord, Action, RiskStatus, ExecutionStatus, MarketSnapshot, PortfolioState, TradeMemory
)
from tradepilot.agents.forensics import ForensicsAgent
from tradepilot.memory.journal import TradeJournal
from tradepilot.memory.store import MemoryStore
from tradepilot.agents.llm_strategy import LLMStrategyAgent

def main():
    print("--- TRADEPILOT FORENSICS & MEMORY DEMO (SIMULATION) ---\n")
    
    # 1. Create a historical TradeRecord
    print("[1] Creating simulated historical TradeRecord (AAPL, Buy @ $150, Exit @ $140)...")
    journal = TradeJournal(db_path="data/demo_journal.db")
    store = MemoryStore(db_path="data/demo_store.db")
    
    trade_id = uuid.uuid4()
    thesis_id = uuid.uuid4()
    
    record = TradeRecord(
        trade_id=trade_id,
        thesis_id=thesis_id,
        symbol="AAPL",
        action=Action.BUY,
        quantity=50,
        original_thesis="Earnings will surprise to the upside and momentum is strong.",
        original_confidence=0.85,
        assumptions=["Q3 revenue > $85B", "SMA50 holds as support"],
        invalidation_conditions=["SMA50 breaks down", "Guidance is weak"],
        risk_decision=RiskStatus.APPROVED,
        entry_price=150.0,
        entry_timestamp=datetime.datetime.now(datetime.timezone.utc),
        exit_price=140.0,
        exit_timestamp=datetime.datetime.now(datetime.timezone.utc),
        realized_pnl=-500.0,
        realized_return_pct=-0.066,
        execution_status=ExecutionStatus.FILLED
    )
    journal.save_record(record)
    print(f"    Saved TradeRecord {trade_id} to journal.")
    
    # 3. Run Forensics
    print("\n[2] Running Forensics Agent on the closed trade...")
    agent = ForensicsAgent()
    forensics = agent.analyze_trade(record)
    
    if forensics:
        print(f"\n    Forensics Result:")
        print(f"    Thesis Correctness: {forensics.thesis_correctness.value}")
        print(f"    Lesson: {forensics.lesson}")
        print(f"    Primary Factor: {forensics.primary_failure_or_success_factor}")
        
        # 4. Store TradeMemory
        print("\n[3] Generating and Storing TradeMemory...")
        memory = TradeMemory(
            source_trade_id=trade_id,
            symbol="AAPL",
            market_condition=forensics.actual_market_conditions,
            lesson=forensics.lesson,
            applicable_conditions=["Earnings", "SMA breakdown"],
            evidence_count=1,
            confidence=forensics.confidence
        )
        store.save_memory(memory)
        print(f"    Saved Memory {memory.memory_id} to store.")
        
        # 5. Retrieve memory
        print("\n[4] Retrieving memory for AAPL...")
        retrieved = store.retrieve_memories("AAPL")
        print(f"    Found {len(retrieved)} memories.")
        
        # 6. Feed to Strategy
        print("\n[5] Feeding retrieved memory into Future Strategy Agent...")
        strategy = LLMStrategyAgent()
        
        # Mock a future snapshot
        snapshot = MarketSnapshot(
            symbol="AAPL",
            current_price=142.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
            volume=2000000,
            previous_close=145.0,
            price_change_pct=-0.02,
            sma_20=148.0,
            sma_50=141.0, # Hovering near SMA50 again
            volatility=0.03
        )
        portfolio = PortfolioState(
            account_equity=100000,
            buying_power=400000,
            cash=100000,
            current_daily_loss=0,
            open_positions=[],
            number_of_open_positions=0
        )
        
        proposal = strategy.generate_proposal(snapshot, portfolio, memories=retrieved)
        
        if proposal:
            print(f"\n    Future AI Decision: {proposal.action.value}")
            print(f"    Memory Influence: {proposal.memory_influence.value}")
            print(f"    Memory Reasoning: {proposal.reasoning.split('Memory Reasoning: ')[-1]}")
        else:
             print("\n    Future AI Decision: NO_TRADE")
             
    else:
        print("Forensics failed to generate.")

if __name__ == "__main__":
    main()
