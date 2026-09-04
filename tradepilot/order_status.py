import argparse
import sys
from tradepilot.alpaca.client import get_alpaca_client

def main():
    parser = argparse.ArgumentParser(description="Check Alpaca Order Status by ID")
    parser.add_argument("order_id", type=str, help="Alpaca Order ID (UUID)")
    args = parser.parse_args()

    client = get_alpaca_client()

    try:
        order = client.get_order_by_id(args.order_id)
        
        print(f"--- ORDER STATUS ---")
        print(f"Symbol:           {order.symbol}")
        print(f"Side:             {order.side.value if hasattr(order.side, 'value') else order.side}")
        print(f"Quantity:         {order.qty}")
        print(f"Status:           {order.status.value if hasattr(order.status, 'value') else order.status}")
        print(f"Filled Quantity:  {order.filled_qty}")
        print(f"Avg Fill Price:   {order.filled_avg_price}")
        print(f"Submitted Time:   {order.submitted_at}")
        print(f"Client Order ID:  {order.client_order_id}")
        print(f"Alpaca Order ID:  {order.id}")
        
    except Exception as e:
        print(f"ERROR fetching order: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
