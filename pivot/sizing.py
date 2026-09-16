"""Dollar targets, independent of the legacy risk engine. Decimal arithmetic only."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid amount') from None
    if not result.is_finite():
        raise ValueError('Amount must be finite')
    return result


def purchase_plan(amount, price, buying_power, direction='long', fractionable=True):
    amount, price, buying_power = map(decimal, (amount, price, buying_power))
    if amount < 1 or amount != amount.quantize(Decimal('.01')) or price <= 0 or buying_power < amount:
        raise ValueError('Enter a positive dollar target within current buying power')
    if direction not in ('long', 'short'):
        raise ValueError('Invalid direction')
    quantum = Decimal('.000000001') if direction == 'long' and fractionable else Decimal('1')
    qty = (amount / price).quantize(quantum, rounding=ROUND_DOWN)
    planned = qty * price
    if qty <= 0 or planned < amount * Decimal('.99'):
        raise ValueError('No supported share quantity fits within 1% of the purchase target')
    return {'target_dollars': str(amount), 'quantity': str(qty), 'planned_dollars': str(planned),
            'fractional': qty != qty.to_integral_value(), 'direction': direction}
