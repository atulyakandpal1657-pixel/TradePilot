import uuid
from tradepilot.memory.journal import TradeJournal
from tradepilot.alpaca.client import get_alpaca_client

trade_id = uuid.UUID("b6e16912-ee7e-47c6-a51b-a663064adaab")
alpaca_order_id = "e3173016-c4fb-4208-ba0d-b99f425d417b"

journal = TradeJournal()
record = journal.get_record(trade_id)

if record:
    client = get_alpaca_client()
    order = client.get_order_by_id(alpaca_order_id)
    record.alpaca_order_id = alpaca_order_id
    record.filled_quantity = float(order.filled_qty)
    record.average_fill_price = float(order.filled_avg_price)
    record.execution_status = order.status.value if hasattr(order.status, 'value') else str(order.status)
    record.entry_price = float(order.filled_avg_price)
    
    # Alpaca status returns 'filled', map it properly if needed, but ExecutionStatus.FILLED is 'FILLED'
    from tradepilot.core.models import ExecutionStatus
    record.execution_status = ExecutionStatus.FILLED
    journal.save_record(record)
    print("Fixed PLTR record.")
else:
    print("Record not found.")
