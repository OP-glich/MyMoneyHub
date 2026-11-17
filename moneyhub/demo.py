"""Deliberately separate demo data, created only in a new empty demo database."""
import secrets
from datetime import date, timedelta

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .db import write
from .models import Owner, Account, Category, Product
from . import finance as f


def seed_demo(engine):
    with write(engine) as s:
        if s.scalar(select(Owner.id)) or s.scalar(select(Account.id)):
            return False
        s.add(Owner(id=1, password_hash=generate_password_hash('ExploreMyMoney!2026')))
        cats = {}
        for label, kind in [('Salary', 'income'), ('Freelance', 'income'),
                            ('Groceries', 'expense'), ('Transport', 'expense'),
                            ('Housing', 'expense'), ('Utilities', 'expense')]:
            c = Category(name=label, kind=kind)
            s.add(c)
            s.flush()
            cats[label] = c.id
        today = date.today()
        start = (today-timedelta(days=45)).isoformat()
        recent = (today-timedelta(days=3)).isoformat()
        key = lambda: secrets.token_hex(20)
        bank = f.create_account(s, 'Everyday bank', '18750.00', start, key())
        wallet = f.create_account(s, 'Cash wallet', '850.00', start, key())
        f.transaction(s, bank.id, cats['Salary'], '22500.00', recent, 'Monthly salary · Windhoek', key())
        f.transaction(s, bank.id, cats['Housing'], '6500.00', recent, 'Monthly rent', key())
        f.transaction(s, bank.id, cats['Groceries'], '1245.80', recent, 'Weekly groceries', key())
        f.transaction(s, wallet.id, cats['Transport'], '180.00', recent, 'Taxi fares', key())
        f.transaction(s, bank.id, cats['Utilities'], '720.00', recent, 'Electricity and water', key())
        f.transaction(s, bank.id, cats['Freelance'], '1800.00', recent, 'Design project paid', key())
        f.saving(s, bank.id, 'Emergency fund', '8000.00', '30000.00', key())
        f.saving(s, bank.id, 'December travel', '2500.00', '7500.00', key())
        for label, amount in [('Groceries', '3500.00'), ('Transport', '1200.00'), ('Housing', '6500.00')]:
            f.budget(s, cats[label], today.strftime('%Y-%m'), amount, key())
        loan_start = today-timedelta(days=20)
        loan = f.create_loan(s, bank.id, 'lend', 'Demo Borrower A', '2500.00', '5.00', '30',
                             loan_start.isoformat(), (loan_start+timedelta(days=30)).isoformat(),
                             '5% fixed for 30 days. Interest first; no late fee.', key(),
                             purpose='School supplies', contact='DEMO-CONTACT-001')
        f.repay(s, loan.id, bank.id, '625.00', recent, 'DEMO-001', key())
        borrowed = f.create_loan(s, bank.id, 'borrow', 'Family loan', '4000.00', '0', '60',
                                 loan_start.isoformat(), (loan_start+timedelta(days=60)).isoformat(),
                                 'Interest free; repay within 60 days.', key(), purpose='Home repairs')
        f.repay(s, borrowed.id, bank.id, '500.00', recent, 'FAMILY-001', key())
        product = Product(name='Handmade notebooks', selling_price=8500)
        s.add(product)
        s.flush()
        f.purchase(s, product.id, bank.id, '20', '45.00', start, key())
        f.purchase(s, product.id, bank.id, '10', '50.00', recent, key())
        f.sell(s, product.id, wallet.id, '8', '85.00', recent, key())
    return True
