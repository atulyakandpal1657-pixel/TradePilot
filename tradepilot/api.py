import os
from typing import Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from tradepilot.memory.journal import TradeJournal
from tradepilot.decision_trace import get_trace_data
from tradepilot.core.config import settings

app = FastAPI(title="TradePilot UI")

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
DB_PATH = "data/tradepilot.db"

@app.get("/api/trace/{trade_id}")
def get_trace(trade_id: str) -> Dict[str, Any]:
    journal = TradeJournal(db_path=DB_PATH)
    data = get_trace_data(journal, trade_id_str=trade_id)
    if data is None:
        # Fallback if they pass alpaca_order_id
        data = get_trace_data(journal, alpaca_order_id=trade_id)
    
    if data is None:
        raise HTTPException(status_code=404, detail="TradeRecord not found")

    # Convert Pydantic models to dicts for JSON serialization
    return {
        "record": data["record"].model_dump(),
        "live_status": data["live_status"],
        "live_filled_qty": data["live_filled_qty"],
        "live_fill_price": data["live_fill_price"],
        "live_submitted": data["live_submitted"],
        "alpaca_oid_live": data["alpaca_oid_live"],
        "client_oid_live": data["client_oid_live"],
        "memories": [m.model_dump() for m in data["memories"]],
        "thesis_text": data["thesis_text"],
        "evidence_text": data["evidence_text"],
        "summary_text": data["summary_text"],
        "memory_reasoning": data["memory_reasoning"],
        "demo_cap": data["demo_cap"],
        "snapshot_price": data["snapshot_price"],
        "actual_fill_price": data["actual_fill_price"],
        "demo_note": data["demo_note"],
        "outcome_line": data["outcome_line"],
        "system_status": {
            "paper_trading": settings.alpaca_paper_trade,
            "demo_cap": settings.max_demo_order_notional
        }
    }

# Ensure web directory exists for mounting
os.makedirs(WEB_DIR, exist_ok=True)

# Mount static files at /
@app.get("/")
def read_index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))

app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
