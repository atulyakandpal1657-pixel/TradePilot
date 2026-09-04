import argparse
import sys
import uuid
from tradepilot.memory.journal import TradeJournal
from tradepilot.core.models import ExecutionStatus

def display_trade_status(trade_id_str: str, db_path: str = "data/tradepilot.db") -> int:
    try:
        trade_id = uuid.UUID(trade_id_str)
    except ValueError:
        print(f"Error: '{trade_id_str}' is not a valid UUID.", file=sys.stderr)
        return 1

    journal = TradeJournal(db_path=db_path)
    record = journal.get_record(trade_id)

    if not record:
        print(f"Error: TradeRecord not found for trade_id {trade_id}", file=sys.stderr)
        return 1

    print("======================================================================")
    print("  TRADEPILOT -- TRADE STATUS")
    print("======================================================================")
    print(f"Trade ID:           {record.trade_id}")
    print(f"Thesis ID:          {record.thesis_id}")
    print(f"Symbol:             {record.symbol}")
    print(f"Action:             {record.action.value}")
    print(f"Requested Quantity: {record.quantity}")
    print(f"Filled Quantity:    {record.filled_quantity}")
    
    avg_price = f"${record.average_fill_price:.2f}" if record.average_fill_price is not None else "N/A"
    print(f"Average Fill Price: {avg_price}")
    
    status_val = record.execution_status.value if hasattr(record.execution_status, "value") else str(record.execution_status)
    print(f"Execution Status:   {status_val}")
    
    alpaca_oid = record.alpaca_order_id if record.alpaca_order_id else "N/A"
    print(f"Alpaca Order ID:    {alpaca_oid}")
    print(f"Client Order ID:    {record.trade_id}")
    
    entry_ts = record.entry_timestamp.isoformat() if record.entry_timestamp else "N/A"
    print(f"Entry Timestamp:    {entry_ts}")
    
    if record.exit_price is not None:
        print(f"Exit Price:         ${record.exit_price:.2f}")
    
    # P&L only if actually closed
    if record.exit_price is not None and record.entry_price is not None and record.filled_quantity > 0:
        pnl = record.realized_pnl if record.realized_pnl is not None else 0.0
        pct = (record.realized_return_pct if record.realized_return_pct is not None else 0.0) * 100
        print(f"Realized P&L:       ${pnl:.2f} ({pct:.2f}%)")

    print("======================================================================")
    return 0

def main():
    parser = argparse.ArgumentParser(description="TradePilot Trade Status CLI (Read-Only)")
    parser.add_argument("trade_id", type=str, help="TradePilot trade ID (UUID)")
    parser.add_argument("--db", type=str, default="data/tradepilot.db", help="Path to TradePilot database")
    
    args = parser.parse_args()
    
    exit_code = display_trade_status(args.trade_id, args.db)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
