import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from src.ibkr_client import IBKRClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--size', type=int, required=True)
    args = parser.parse_args()

    symbol = args.symbol
    side = args.side
    size = args.size

    load_dotenv()

    host = os.getenv('IBKR_HOST', '127.0.0.1')
    port = int(os.getenv('IBKR_PORT', '7497'))
    client_id = int(os.getenv('IBKR_EXEC_CLIENT_ID', '3'))

    trades_file = Path('trades.csv')

    ibkr = None
    try:
        ibkr = IBKRClient(host, port, client_id)
        trade = ibkr.place_order(symbol, side, size)

        # Check if order was rejected
        if trade.orderStatus.status in {'Cancelled', 'ApiCancelled', 'Inactive'}:
            print(f"Order rejected: {trade.orderStatus.status}")
            sys.exit(1)

        # Append to trades.csv
        tz = ZoneInfo('America/New_York')
        now_utc = datetime.now(timezone.utc).isoformat()
        fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus.avgFillPrice else 0
        order_id = trade.order.orderId
        status = trade.orderStatus.status

        retries = 0
        while retries < 2:
            try:
                with open(trades_file, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([now_utc, symbol, side, size, fill_price, order_id, status])
                break
            except IOError:
                retries += 1
                if retries < 2:
                    time.sleep(1)
                else:
                    print("trades.csv locked")
                    sys.exit(1)

        print(f"Order placed: {symbol} {side} {size} @ {fill_price} (status: {status})")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        if ibkr:
            ibkr.disconnect()


if __name__ == '__main__':
    main()
