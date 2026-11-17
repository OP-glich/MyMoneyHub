from datetime import date, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from moneyhub import finance as f
from moneyhub.db import write, engine_for
from moneyhub.models import Entry, Account, Operation, Saving, Loan, Repayment, Lot, Sale, Budget


@pytest.mark.parametrize('value,expected', [('0.01', 1), ('1234.56', 123456), ('-1.23', -123), (Decimal('1.10'), 110), ('0', 0)])
def test_exact_money(value, expected):
    assert f.money(value) == expected


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '0.001', 'abc', '1000000000000.01', None])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        f.money(value)


def test_cash_and_persistence(db, key, today):
    engine, path = db
    with write(engine) as s:
        f.transaction(s, 1, 1, '100.25', today, 'Income', key())
        f.transaction(s, 1, 2, '30.12', today, 'Expense', key())
        f.transfer(s, 1, 2, '50.01', today, key())
        assert f.balance(s, 1) == 1002012
        assert f.balance(s, 2) == 55001
    second = engine_for(path)
    with Session(second) as s:
        assert f.balance(s, 1) == 1002012
        assert f.balance(s, 2) == 55001
        assert f.total(s, Entry, Entry.amount) == 1057013
    second.dispose()


def test_protected_savings_and_atomic_rollback(db, key, today):
    engine, _ = db
    with write(engine) as s:
        goal = f.saving(s, 1, 'Emergency', '9500', '10000', key())
        s.flush()
        goal_id = goal.id
        assert f.balance(s, 1) == 1000000
        assert f.spendable(s, 1) == 50000
    with pytest.raises(ValueError, match='Insufficient'):
        with write(engine) as s:
            f.transaction(s, 1, 2, '500.01', today, 'Blocked', key())
    with write(engine) as s:
        assert f.balance(s, 1) == 1000000
        f.saving(s, 1, 'Emergency', '9000', '10000', key(), goal_id)
        f.transaction(s, 1, 2, '1000', today, 'Allowed after release', key())
        assert f.balance(s, 1) == 900000
        assert f.spendable(s, 1) == 0
    with pytest.raises(ValueError):
        with write(engine) as s:
            f.saving(s, 1, 'Extra', '0.01', '100', key())


def test_submission_key_is_unique(db, key, today):
    engine, _ = db
    token = key()
    with write(engine) as s:
        f.transaction(s, 1, 2, '10', today, 'One', token)
    with pytest.raises(ValueError, match='already'):
        with write(engine) as s:
            f.transaction(s, 1, 2, '10', today, 'One', token)
    with Session(engine) as s:
        assert f.balance(s, 1) == 999000


def loan(s, key, direction='lend', principal='1000', rate='10'):
    start = date.today()-timedelta(days=30)
    return f.create_loan(s, 1, direction, 'Anna', principal, rate, '30', start.isoformat(), date.today().isoformat(), 'Fixed term; no late fees', key())


def test_simple_interest_partial_repayments(db, key, today):
    engine, _ = db
    with write(engine) as s:
        l = loan(s, key)
        ident = l.id
        assert l.interest == 10000
        assert f.balance(s, 1) == 900000
        assert f.outstanding(s, l) == (100000, 10000)
        p = f.repay(s, l.id, 1, '150', today, 'PAY-1', key())
        assert (p.interest, p.principal) == (10000, 5000)
        assert f.outstanding(s, l) == (95000, 0)
    with pytest.raises(ValueError, match='reference'):
        with write(engine) as s:
            f.repay(s, ident, 1, '150', today, 'PAY-1', key())
    with pytest.raises(ValueError, match='exceeds'):
        with write(engine) as s:
            f.repay(s, ident, 1, '950.01', today, 'PAY-2', key())
    with write(engine) as s:
        f.repay(s, ident, 1, '950', today, 'PAY-2', key())
        assert f.outstanding(s, s.get(Loan, ident)) == (0, 0)
        assert f.balance(s, 1) == 1010000


def test_interest_rounding_and_borrowing(db, key, today):
    engine, _ = db
    with write(engine) as s:
        l = loan(s, key, 'borrow', '1.01', '0.50')
        assert l.interest == 1  # 0.505 cents rounds half up
        assert f.balance(s, 1) == 1000101
        f.repay(s, l.id, 1, '1.02', today, 'B-1', key())
        assert f.balance(s, 1) == 999999


def test_zero_interest_and_invalid_due_date(db, key, today):
    engine, _ = db
    with write(engine) as s:
        l = loan(s, key, 'lend', '20', '0')
        assert l.interest == 0
    with pytest.raises(ValueError, match='Due date'):
        with write(engine) as s:
            f.create_loan(s, 1, 'lend', 'Anna', '100', '5', '30', today, today, 'Terms', key())


def test_fifo_profit_and_negative_stock(db, key, today):
    engine, _ = db
    with write(engine) as s:
        f.purchase(s, 1, 1, '3', '10.10', today, key())
        f.purchase(s, 1, 1, '2', '12.20', today, key())
        sale = f.sell(s, 1, 2, '4', '20', today, key())
        assert sale.cost == 4250
        assert sale.revenue == 8000
        assert sale.revenue-sale.cost == 3750
        assert f.stock(s, 1) == 1
    with pytest.raises(ValueError, match='stock'):
        with write(engine) as s:
            f.sell(s, 1, 2, '2', '20', today, key())
    with Session(engine) as s:
        assert f.stock(s, 1) == 1
        assert len(list(s.scalars(select(Sale)))) == 1
        assert f.balance(s, 1) == 994530
        assert f.balance(s, 2) == 58000


def test_budget_upsert(db, key, today):
    engine, _ = db
    month = today[:7]
    with write(engine) as s:
        f.budget(s, 2, month, '100', key())
        s.flush()
        f.budget(s, 2, month, '200', key())
    with Session(engine) as s:
        assert s.scalar(select(Budget.amount)) == 20000
        assert len(list(s.scalars(select(Budget)))) == 1
    with pytest.raises(ValueError):
        with write(engine) as s:
            f.budget(s, 1, month, '100', key())


def test_concurrent_spending_is_serialized(db, key, today):
    engine, _ = db
    def spend(_):
        try:
            with write(engine) as s:
                f.transaction(s, 1, 2, '7000', today, 'Concurrent', key())
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(spend, range(2))) == [False, True]
    with Session(engine) as s:
        assert f.balance(s, 1) == 300000


def test_inventory_cannot_be_backdated(db, key, today):
    engine, _ = db
    with write(engine) as s:
        f.purchase(s, 1, 1, '2', '10', today, key())
    with pytest.raises(ValueError, match='date order'):
        with write(engine) as s:
            f.purchase(s, 1, 1, '2', '10', (date.today()-timedelta(days=1)).isoformat(), key())
