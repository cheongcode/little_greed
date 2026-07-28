import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from src.ibkr_client import IBKRClient
import strategy


def et_now():
    tz = ZoneInfo('America/New_York')
    return datetime.now(tz).strftime('%H:%M:%S')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()

    symbol = args.symbol
    check_only = args.check_only

    # 1. Load .env
    load_dotenv()

    # 2. Hard guard: paper vs live port
    paper_trading = os.getenv('PAPER_TRADING', 'false').lower() == 'true'
    port = int(os.getenv('IBKR_PORT', '7497'))

    if paper_trading and port in {7496, 4001}:
        print(f"[{et_now()}] ABORT: paper flag but live port {port}")
        sys.exit(1)
    if not paper_trading and port in {7497, 11002}:
        print(f"[{et_now()}] ABORT: live flag but paper port {port}")
        sys.exit(1)

    # 3. Check daily trade limit
    trades_file = Path('trades.csv')
    if not trades_file.exists():
        trades_file.write_text('timestamp_iso,symbol,side,size,fill_price,order_id,status\n')

    tz = ZoneInfo('America/New_York')
    today_date = datetime.now(tz).date()

    buy_count = 0
    with open(trades_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('timestamp_iso', '').startswith(str(today_date)) and row.get('side') == 'BUY':
                buy_count += 1

    max_trades = int(os.getenv('MAX_TRADES_PER_DAY', '5'))
    if buy_count >= max_trades:
        print(f"[{et_now()}] Daily trade limit reached ({buy_count}/{max_trades})")
        sys.exit(0)

    # 4. Connect
    host = os.getenv('IBKR_HOST', '127.0.0.1')
    port = int(os.getenv('IBKR_PORT', '7497'))
    client_id = int(os.getenv('IBKR_CLIENT_ID', '2'))

    try:
        ibkr = IBKRClient(host, port, client_id)
    except Exception as e:
        print(f"[{et_now()}] Connection failed: {e}")
        sys.exit(1)

    try:
        # 5. Evaluate strategy
        result = strategy.evaluate(symbol, ibkr.ib)
        print(f"[{et_now()}] Strategy result: {result}")

        # 6. If check-only, exit
        if check_only:
            print(f"[{et_now()}] Check-only mode, exiting")
            ibkr.disconnect()
            sys.exit(0)

        # 7. If not pass, exit
        if not result['pass']:
            print(f"[{et_now()}] Strategy failed: {result['reasons']}")
            ibkr.disconnect()
            sys.exit(0)

        # 8. Size position
        price = result['price']
        max_trade_usd = float(os.getenv('MAX_TRADE_SIZE_USD', '2500'))
        portfolio_value = float(os.getenv('PORTFOLIO_VALUE_USD', '25000'))
        budget = min(max_trade_usd, portfolio_value * 0.10)
        quantity = int(budget / price)

        if quantity < 1:
            print(f"[{et_now()}] Position too small (qty={quantity}, price={price})")
            ibkr.disconnect()
            sys.exit(0)

        print(f"[{et_now()}] Sized position: qty={quantity}, price={price}, budget={budget}")

        # 9. Spawn trade.py subprocess
        cmd = [
            'python3', 'trade.py',
            '--symbol', symbol,
            '--side', 'BUY',
            '--size', str(quantity)
        ]

        try:
            result_proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            print(f"[{et_now()}] {result_proc.stdout}")
            if result_proc.returncode != 0:
                print(f"[{et_now()}] trade.py failed: {result_proc.stderr}")
        except subprocess.TimeoutExpired:
            print(f"[{et_now()}] trade.py timeout (30s)")
            sys.exit(1)

        # 10. Read last row of trades.csv
        with open(trades_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if rows:
                last_row = rows[-1]
                status = last_row.get('status', 'unknown')
                print(f"[{et_now()}] Order status: {status}")

    finally:
        ibkr.disconnect()


if __name__ == '__main__':
    main()
