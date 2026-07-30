import time

import pandas as pd
from ib_async import IB, Stock, MarketOrder


class IBKRClient:
    def __init__(self, host, port, client_id):
        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)

    def place_order(self, symbol, side, quantity):
        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)

        order = MarketOrder(side.upper(), quantity)
        order.outsideRth = True

        trade = self.ib.placeOrder(contract, order)

        start = time.time()
        while time.time() - start < 10:
            time.sleep(0.1)
            if trade.orderStatus.status != 'PendingSubmit':
                break

        return trade

    def get_historical_bars(self, symbol: str, bar_size: str, duration: str) -> pd.DataFrame:
        """Fetch historical bars via IBKR reqHistoricalData."""
        from src.data_shim import get_bars
        # Estimate n_bars from duration string for get_bars; delegate directly via shim
        contract = Stock(symbol, 'SMART', 'USD')
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=False,
            keepUpToDate=False,
        )
        time.sleep(0.1)
        if not bars:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='DELAYED_FROZEN_LAST',
                useRTH=False,
                keepUpToDate=False,
            )
            time.sleep(0.1)
        from src.data_shim import _bars_to_df
        return _bars_to_df(bars)

    def get_live_bars(self, symbol: str, bar_size: str = '5 mins', lookback_bars: int = 78) -> pd.DataFrame:
        """Fetch recent bars with keepUpToDate=False for real-time alignment."""
        from src.data_shim import get_bars
        return get_bars(symbol, bar_size, lookback_bars, self.ib)

    def disconnect(self):
        self.ib.disconnect()
