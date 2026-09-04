import sqlite3
import uuid
import os
from typing import List, Optional
from tradepilot.core.models import TradeMemory, MarketSnapshot

class MemoryStore:
    """
    SQLite-backed store for structured TradeMemory objects.
    """
    def __init__(self, db_path: str = "data/tradepilot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_memories (
                    memory_id TEXT PRIMARY KEY,
                    source_trade_id TEXT,
                    symbol TEXT,
                    confidence REAL,
                    evidence_count INTEGER,
                    data JSON
                )
            """)
            conn.commit()
            
    def save_memory(self, memory: TradeMemory):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trade_memories (memory_id, source_trade_id, symbol, confidence, evidence_count, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET 
                    confidence=excluded.confidence,
                    evidence_count=excluded.evidence_count,
                    data=excluded.data
                """,
                (
                    str(memory.memory_id),
                    str(memory.source_trade_id),
                    memory.symbol,
                    memory.confidence,
                    memory.evidence_count,
                    memory.model_dump_json()
                )
            )
            conn.commit()
            
    def retrieve_memories(self, symbol: str, limit: int = 3) -> List[TradeMemory]:
        """
        Deterministic retrieval function.
        Prioritizes:
        1. Exact symbol match
        2. Higher evidence count
        3. High confidence
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT data FROM trade_memories 
                WHERE symbol = ?
                ORDER BY evidence_count DESC, confidence DESC
                LIMIT ?
                """, 
                (symbol, limit)
            )
            
            return [TradeMemory.model_validate_json(row[0]) for row in cursor.fetchall()]
