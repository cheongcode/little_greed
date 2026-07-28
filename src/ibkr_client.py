import time
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

    def disconnect(self):
        self.ib.disconnect()
