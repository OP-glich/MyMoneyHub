from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from moneyhub import finance as f
from moneyhub.db import write, migrate_connection, validate_database, engine_for, SCHEMA_VERSION
from moneyhub.models import Account, Entry, Category, Loan, LoanContract, Product, Repayment, Saving, Preference
from moneyhub.receipts import whatsapp_message
from moneyhub.web import create_app


@pytest.fixture
def ui(tmp_path):
    app = create_app(tmp_path/'everyday.db', testing=True)
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as sess:
        csrf = sess['csrf']
    client.post('/login', data=dict(csrf=csrf, password='SafePersonalPassword!', confirm='SafePersonalPassword!'))
    yield app, client
    app.extensions['engine'].dispose()


def post(client, action, key, **fields):
    with client.session_transaction() as sess:
        csrf = sess['csrf']
    return client.post('/action/'+action, data=dict(csrf=csrf, key=key, **fields), follow_redirects=True)


def test_automatic_balance_optional_categories_and_protected_money(ui, today):
    app, client = ui
    html = client.get('/transactions').data.decode()
    assert 'name="account_id"' not in html
    assert 'Choose account' not in html
    assert post(client, 'transaction', 'income-1', section='transactions', kind='income', amount='1000.15', date=today).status_code == 200
    assert b'Saved successfully.' in post(client, 'transaction', 'expense-1', section='transactions', kind='expense', amount='50.05', date=today).data
    post(client, 'saving', 'goal-1', section='savings', name='Rainy day', amount='900', target='2000')
    response = post(client, 'transaction', 'too-much', section='transactions', kind='expense', amount='51', date=today)
    assert b'Insufficient spendable' in response.data
    with Session(app.extensions['engine']) as s:
        assert len(list(s.scalars(select(Account)))) == 1
        assert f.balance(s, 1) == 95010
        assert f.spendable(s, 1) == 5010
    assert b'N$ 50.10' in client.get('/dashboard').data


def test_multiple_balances_never_merged(ui, today):
    app, client = ui
    with write(app.extensions['engine']) as s:
        a = f.create_account(s, 'Original bank', '500', today, 'legacy-a')
        b = f.create_account(s, 'Original wallet', '200', today, 'legacy-b')
    post(client, 'transaction', 'simple-new', section='transactions', kind='income', amount='10', date=today)
    with Session(app.extensions['engine']) as s:
        assert f.balance(s, a.id) == 51000
        assert f.balance(s, b.id) == 20000
    post(client, 'default_balance', 'choose-b', section='settings', balance_id=b.id)
    post(client, 'transaction', 'simple-next', section='transactions', kind='expense', amount='1', date=today)
    with Session(app.extensions['engine']) as s:
        assert f.balance(s, a.id) == 51000
        assert f.balance(s, b.id) == 19900
        assert s.get(Preference, 1).account_id == b.id


def test_monthly_budget_no_double_count_and_near_warning(ui, today):
    app, client = ui
    post(client, 'transaction', 'earn', section='transactions', kind='income', amount='2000', date=today)
    with Session(app.extensions['engine']) as s:
        cat = s.scalar(select(Category).where(Category.name == 'Food')).id
    post(client, 'transaction', 'food', section='transactions', kind='expense', category_id=cat, amount='850', date=today)
    post(client, 'monthly_budget', 'monthly', section='budgets', month=today[:7], amount='1000')
    post(client, 'budget', 'food-budget', section='budgets', month=today[:7], category_id=cat, amount='900')
    html = client.get('/budgets').data
    assert b'N$ 150.00' in html
    assert b'at least 80%' in html
    assert b'N$ 150.00' in client.get('/dashboard').data


def test_pending_payout_repayment_and_saved_receipts(ui, today):
    app, client = ui
    due = (date.today()+timedelta(days=30)).isoformat()
    post(client, 'transaction', 'fund', section='transactions', kind='income', amount='3000', date=today)
    response = post(client, 'loan_plan', 'agreement-1', section='loans', person='Demo Borrower A', purpose='School supplies',
                    amount='1000', rate='20', date=today, due=due, contact='DEMO-CONTACT-001')
    assert b'Pending' in response.data
    with Session(app.extensions['engine']) as s:
        contract = s.scalar(select(LoanContract))
        ident = contract.id
        assert contract.interest == 20000
        assert contract.contact == 'DEMO-CONTACT-001'
        assert contract.loan_id is None
        assert f.balance(s, 1) == 300000
        message = whatsapp_message(contract, 'Pending')
        for text in ['Demo Borrower A','School supplies','N$ 1,000.00','20% fixed','N$ 200.00','N$ 1,200.00',today,due,'Pending']:
            assert text in message
    for suffix in ['', '.pdf', '.txt', '?pdf=1', '?format=txt']:
        pending=client.get(f'/loans/{ident}/receipt'+suffix)
        assert pending.status_code==409
        assert pending.mimetype=='text/html'
        assert b'No payout receipt' in pending.data
    post(client, 'payout', 'pay-1', section='loans', contract_id=ident, date=today)
    duplicate = post(client, 'payout', 'pay-2', section='loans', contract_id=ident, date=today)
    assert b'already has a payout' in duplicate.data
    with Session(app.extensions['engine']) as s:
        contract = s.get(LoanContract, ident)
        loan_id = contract.loan_id
        assert f.balance(s, 1) == 200000
        assert len(list(s.scalars(select(Loan)))) == 1
    assert b'Given Out' in client.get(f'/loans/{ident}/receipt').data
    post(client, 'repayment', 'repay-1', section='loans', loan_id=loan_id, amount='300', date=today, reference='BANK-001')
    duplicate = post(client, 'repayment', 'repay-2', section='loans', loan_id=loan_id, amount='300', date=today, reference='BANK-001')
    assert b'already recorded' in duplicate.data
    excessive = post(client, 'repayment', 'repay-3', section='loans', loan_id=loan_id, amount='901', date=today)
    assert b'exceeds' in excessive.data
    receipt = client.get('/repayments/1/receipt').data
    assert b'REPAYMENT RECEIPT' in receipt and b'N$ 900.00' in receipt
    with Session(app.extensions['engine']) as s:
        assert f.balance(s, 1) == 230000
        assert f.outstanding(s, s.get(Loan, loan_id)) == (90000, 0)
    post(client, 'repayment', 'repay-4', section='loans', loan_id=loan_id, amount='900', date=today)
    assert b'Repaid With Interest' in client.get('/loans?status=repaid').data
    # Earlier payment receipts retain the balance after that payment.
    assert b'N$ 900.00' in client.get('/repayments/1/receipt').data
    assert b'N$ 0.00' in client.get('/repayments/2/receipt').data


def test_annual_simple_preview_and_invalid_pending_payment(ui, today):
    app, client = ui
    due = (date.today()+timedelta(days=365)).isoformat()
    query = dict(amount='1000', rate='10', basis='annual', date=today, due=due)
    preview = client.get('/loan-preview', query_string=query)
    assert preview.json == {'interest':'N$ 100.00','total':'N$ 1,100.00'}
    post(client, 'loan_plan', 'annual-plan', section='loans', person='Example Borrower B', purpose='Tools', **query)
    with Session(app.extensions['engine']) as s:
        c = s.scalar(select(LoanContract))
        assert c.interest == 10000 and c.basis == 'annual' and c.loan_id is None
        assert f.balance(s, c.account_id) == 0
    assert b'does not exist' in post(client, 'repayment', 'pending-fail', section='loans', loan_id=1, amount='1', date=today).data
    assert client.get('/loan-preview', query_string={**query,'amount':'0.001'}).status_code == 400


def test_simple_sales_and_category_rename(ui, today):
    app, client = ui
    post(client, 'transaction', 'fund-items', section='transactions', kind='income', amount='1000', date=today)
    post(client, 'item', 'buy-items', section='inventory', name='Notebooks', quantity='5', amount='10.10', selling_price='20', date=today)
    post(client, 'sale', 'sell-items', section='inventory', product_id=1, quantity=2, amount='20', date=today)
    html = client.get('/sales').data
    assert b'N$ 19.80' in html
    with Session(app.extensions['engine']) as s:
        assert f.stock(s, 1) == 3
        assert f.balance(s, 1) == 98950
        cat = s.scalar(select(Category).where(Category.kind == 'income')).id
        assert s.get(Product, 1).selling_price == 2000
    post(client, 'category_edit', 'rename', section='settings', id=cat, name='My earnings')
    assert b'My earnings' in client.get('/settings').data


def test_full_v1_upgrade_preserves_history_and_verified_backup(tmp_path):
    path = tmp_path/'existing.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        migrate_connection(conn, target_version=1)
        conn.executescript("""
        INSERT INTO accounts VALUES (1,'Original bank'),(2,'Original cash');
        INSERT INTO categories VALUES (1,'Salary','income'),(2,'Food','expense');
        INSERT INTO operations VALUES ('open','account'),('lent','loan'),('paid','repayment'),('stock','purchase');
        INSERT INTO entries VALUES (1,1,NULL,'open','2026-01-01','opening',500000,'Starting money',NULL),
          (2,2,NULL,'open','2026-01-01','opening',20000,'Cash',NULL),
          (3,1,NULL,'lent','2026-01-02','loan',-100000,'Loan',NULL),
          (4,1,NULL,'paid','2026-01-03','repayment',30000,'Part payment',NULL),
          (5,1,NULL,'stock','2026-01-03','purchase',-10000,'Items',NULL);
        INSERT INTO loans VALUES (1,1,3,'lend','Anna',100000,2000,30,20000,'2026-01-02','2026-02-01','Education');
        INSERT INTO repayments VALUES (1,1,4,'PAY-001',10000,20000);
        INSERT INTO budgets VALUES (1,2,'2026-01',50000);
        INSERT INTO savings VALUES (1,1,'Cushion',100000,300000);
        INSERT INTO products VALUES (1,'Notebooks');
        INSERT INTO lots VALUES (1,1,5,10,10,1000);
        """)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {t:(list(r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')),conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()) for t in tables}
    engine = engine_for(path)
    engine.dispose()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        for table,(columns,rows) in before.items():
            fields = ','.join('"'+c+'"' for c in columns)
            assert conn.execute(f'SELECT {fields} FROM "{table}" ORDER BY rowid').fetchall() == rows
        assert conn.execute('SELECT loan_id,interest,basis FROM loan_contracts').fetchone() == (1,20000,'fixed')
    backups = list((tmp_path/'backups').glob('before-upgrade-v1-*.db'))
    assert len(backups) == 1
    validate_database(backups[0])
    with closing(sqlite3.connect(backups[0])) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert conn.execute('SELECT SUM(amount) FROM entries').fetchone()[0] == 440000
    engine = engine_for(path)
    engine.dispose()
    assert len(list((tmp_path/'backups').glob('before-upgrade-v1-*.db'))) == 1


def test_upgrade_aborts_if_backup_cannot_be_verified(tmp_path, monkeypatch):
    path=tmp_path/'blocked-upgrade.db'
    with closing(sqlite3.connect(path)) as conn:
        migrate_connection(conn,target_version=1)
        conn.execute("INSERT INTO accounts(name) VALUES ('Do not change')")
        conn.commit()
        def fail(*_):
            raise ValueError('Backup verification failed')
        monkeypatch.setattr('moneyhub.db.backup',fail)
        with pytest.raises(ValueError,match='Backup verification'):
            migrate_connection(conn)
        assert conn.execute('PRAGMA user_version').fetchone()[0]==1
        assert conn.execute('SELECT name FROM accounts').fetchone()[0]=='Do not change'
        assert 'selling_price' not in [r[1] for r in conn.execute('PRAGMA table_info(products)')]


def test_automatic_receipt_does_not_duplicate_same_day_payment(ui, today):
    app, client=ui
    due=(date.today()+timedelta(days=30)).isoformat()
    post(client,'transaction','earn-before-loan',kind='income',amount='2000',date=today,section='transactions')
    post(client,'loan_plan','record-loan',person='Example Borrower B',purpose='Tools',amount='1000',rate='0',
         date=today,due=due,pay_now='yes',section='loans')
    with Session(app.extensions['engine']) as s:
        assert s.get(LoanContract,1).loan_id is None
    post(client,'payout','actual-confirmation',contract_id=1,date=today,section='loans')
    post(client,'repayment','first-payment',loan_id=1,amount='100',date=today,section='loans')
    duplicate=post(client,'repayment','new-form-same-payment',loan_id=1,amount='100',date=today,section='loans')
    assert b'already saved for this date' in duplicate.data
    with Session(app.extensions['engine']) as s:
        assert len(list(s.scalars(select(Repayment))))==1
        assert f.balance(s,1)==110000
    separate=post(client,'repayment','separate-payment',loan_id=1,amount='100',date=today,
                  reference='SECOND-ACTUAL-PAYMENT',section='loans')
    assert b'Saved successfully.' in separate.data
