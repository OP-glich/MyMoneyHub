"""Exact NAD parsing and formatting, independent of web/persistence libraries."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MAX_CENTS = 100_000_000_000_000


def money(value, positive=False):
    if isinstance(value, float):
        raise ValueError('Use a decimal string or Decimal, not floating point.')
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value != value.quantize(Decimal('.01')):
            raise ValueError('Use at most two decimal places.')
        cents = int(value * 100)
        if abs(cents) > MAX_CENTS or (positive and cents <= 0):
            raise ValueError('Amount is outside the allowed range.')
        return cents
    except (InvalidOperation, OverflowError):
        raise ValueError('Enter a valid monetary amount.') from None


def nad(cents):
    return f'N$ {Decimal(cents) / 100:,.2f}'


def term_interest(principal, rate_bps):
    """Fixed simple interest for the whole agreed term; round once, half up."""
    if principal <= 0 or rate_bps < 0:
        raise ValueError('Invalid principal or interest rate.')
    return int((Decimal(principal)*rate_bps/10000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def loan_interest(principal, rate_bps, basis, days):
    if basis == 'fixed':
        return term_interest(principal, rate_bps)
    if basis != 'annual' or days <= 0:
        raise ValueError('Choose fixed term or annual simple interest.')
    return int((Decimal(principal)*rate_bps*days/365/10000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def split_payment(amount, principal, interest):
    """Return principal-paid, interest-paid, without modifying cash or debt."""
    if amount <= 0 or principal < 0 or interest < 0:
        raise ValueError('Invalid repayment amount or outstanding balance.')
    if amount > principal + interest:
        raise ValueError('Payment exceeds the outstanding balance.')
    paid_interest = min(amount, interest)
    return amount-paid_interest, paid_interest


def fifo_plan(lots, quantity):
    """Input: ordered (lot_id, remaining, unit_cost). Output: (id, units, cost)."""
    if quantity <= 0:
        raise ValueError('Quantity must be positive.')
    allocations = []
    left = quantity
    for ident, remaining, unit_cost in lots:
        if remaining < 0 or unit_cost <= 0:
            raise ValueError('Invalid stock lot.')
        used = min(left, remaining)
        if used:
            allocations.append((ident, used, used*unit_cost))
            left -= used
        if left == 0:
            break
    if left:
        raise ValueError('Sale exceeds available stock.')
    return allocations
