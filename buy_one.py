import time
from ib_async import IB, Stock, MarketOrder

ib = IB()
try:
    ib.connect('127.0.0.1', 7497, clientId=10)

    # Qualify the contract
    contract = Stock('MU', 'SMART', 'USD')
    ib.qualifyContracts(contract)

    # Create market order
    order = MarketOrder('BUY', 1)
    order.outsideRth = True

    # Place the order
    trade = ib.placeOrder(contract, order)

    # Wait up to 10 seconds for order status to settle (not PendingSubmit)
    start = time.time()
    while time.time() - start < 10:
        time.sleep(0.1)
        if trade.orderStatus.status != 'PendingSubmit':
            break

    # Extract results
    order_id = trade.order.orderId
    fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus.avgFillPrice else 'pending'
    status = trade.orderStatus.status

    print(f'Order ID: {order_id}')
    print(f'Fill Price: {fill_price}')
    print(f'Status: {status}')

except Exception as e:
    print(f'Error: {e}')
    raise
finally:
    ib.disconnect()
