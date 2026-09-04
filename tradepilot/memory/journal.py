import sqlite3
import json
import uuid
import os
from typing import List, Optional
from tradepilot.core.models import TradeRecord

class TradeJournal:
    """
    SQLite-backed journal for TradeRecords.
    """
    def __init__(self, db_path: str = "data/tradepilot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    trade_id TEXT PRIMARY KEY,
                    thesis_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    data JSON
                )
            """)
            conn.commit()
            
    def save_record(self, record: TradeRecord):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trade_records (trade_id, thesis_id, symbol, action, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET data=excluded.data
                """,
                (
                    str(record.trade_id),
                    str(record.thesis_id),
                    record.symbol,
                    record.action.value,
                    record.model_dump_json()
                )
            )
            conn.commit()
            
    def get_record(self, trade_id: uuid.UUID) -> Optional[TradeRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT data FROM trade_records WHERE trade_id = ?", (str(trade_id),))
            row = cursor.fetchone()
            if row:
                return TradeRecord.model_validate_json(row[0])
            return None

    def get_record_by_alpaca_order_id(self, alpaca_order_id: str) -> Optional[TradeRecord]:
        """Look up a TradeRecord by Alpaca's own order UUID (stored in data JSON)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM trade_records WHERE json_extract(data, '$.alpaca_order_id') = ?",
                (alpaca_order_id,)
            )
            row = cursor.fetchone()
            if row:
                return TradeRecord.model_validate_json(row[0])
            return None

    def list_all_records(self) -> List[TradeRecord]:
        """Return all TradeRecords, newest first (by rowid)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM trade_records ORDER BY rowid DESC"
            )
            return [TradeRecord.model_validate_json(row[0]) for row in cursor.fetchall()]
