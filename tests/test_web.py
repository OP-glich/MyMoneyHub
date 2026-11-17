import io
import re

import pytest
from sqlalchemy.orm import Session

from moneyhub.web import create_app
from moneyhub.demo import seed_demo
from moneyhub.models import Owner
from moneyhub import finance as f


def token(client):
    with client.session_transaction() as sess:
        return sess['csrf']


@pytest.fixture
def app(tmp_path):
    instance = create_app(tmp_path/'web.db', testing=True)
    yield instance
    instance.extensions['engine'].dispose()


def setup(client):
    assert client.get('/login').status_code == 200
    return client.post('/login', data={'csrf': token(client), 'password': 'MyStrongPassword123!', 'confirm': 'MyStrongPassword123!'})


def test_authentication_csrf_and_password_hash(app):
    client = app.test_client()
    assert client.get('/accounts').status_code == 302
    assert client.post('/login', data={'password': 'oops'}).status_code == 400
    assert setup(client).status_code == 302
    with Session(app.extensions['engine']) as s:
        owner = s.get(Owner, 1)
        assert owner.password_hash != 'MyStrongPassword123!'
        assert owner.password_hash.startswith(('scrypt:', 'pbkdf2:'))
    assert client.post('/action/account', data={'name': 'Bank'}).status_code == 400
    assert client.post('/logout', data={'csrf': token(client)}).status_code == 302
    assert client.get('/reports').status_code == 302
    client.get('/login')
    assert client.post('/login', data={'csrf': token(client), 'password': 'wrong'}).status_code == 401
    assert client.post('/login', data={'csrf': token(client), 'password': 'MyStrongPassword123!'}).status_code == 302


@pytest.mark.parametrize('section', ['dashboard','accounts','transactions','budgets','savings','loans','inventory','reports','settings'])
def test_all_pages_render_empty(app, section):
    client = app.test_client()
    setup(client)
    response = client.get('/'+section)
    assert response.status_code == 200
    assert b'MyMoney Hub' in response.data
    assert b'no-store' in response.headers['Cache-Control'].encode()
    assert 'frame-ancestors' in response.headers['Content-Security-Policy']


def test_all_pages_render_real_demo_and_persist(tmp_path):
    app = create_app(tmp_path/'demo'/'moneyhub.db', testing=True, demo=True)
    assert seed_demo(app.extensions['engine']) is True
    assert seed_demo(app.extensions['engine']) is False
    client = app.test_client()
    client.get('/login')
    client.post('/login', data={'csrf': token(client), 'password': 'ExploreMyMoney!2026'})
    for page in ['dashboard','accounts','transactions','budgets','savings','loans','inventory','reports','settings']:
        response = client.get('/'+page)
        assert response.status_code == 200, page
        assert b'DEMO DATABASE' in response.data
    savings = client.get('/savings').data
    assert b'value="8000.00"' in savings
    assert b'Demo Borrower A' in client.get('/loans').data
    assert b'Handmade notebooks' in client.get('/inventory').data
    assert b'22500.00' in client.get('/export/ledger.csv').data
    app.extensions['engine'].dispose()
    second = create_app(tmp_path/'demo'/'moneyhub.db', testing=True, demo=True)
    with Session(second.extensions['engine']) as s:
        assert f.balance(s, 1) > 0
        assert f.stock(s, 1) == 22
    second.extensions['engine'].dispose()


def test_browser_forms_persist_filter_and_export(app):
    from datetime import date
    client = app.test_client()
    setup(client)
    def post(action, **fields):
        return client.post('/action/'+action, data=dict(csrf=token(client), key=action+'-unique', **fields), follow_redirects=True)
    today = date.today().isoformat()
    response = post('account', name='Bank', amount='1000.00', date=today, section='accounts')
    assert b'Saved successfully' in response.data
    post('transaction', account_id=1, category_id=1, amount='100.00', date=today, note='A special salary', section='transactions')
    result = client.get('/transactions?q=special&account=1')
    assert b'A special salary' in result.data
    assert b'Opening balance' not in result.data
    csv = client.get('/export/ledger.csv?q=special')
    assert b'A special salary' in csv.data
    assert b'Opening balance' not in csv.data
    assert client.get('/transactions?from=invalid').status_code == 400
    download = client.post('/backup', data={'csrf': token(client)})
    assert download.status_code == 200
    assert download.data.startswith(b'SQLite format 3')
    download.close()
    with Session(app.extensions['engine']) as s:
        assert f.balance(s, 1) == 110000


def test_unknown_host_rejected(app):
    client = app.test_client()
    assert client.get('/login', base_url='http://attacker.example').status_code == 400


def test_local_assets_only(app):
    client = app.test_client()
    setup(client)
    html = client.get('/dashboard').data.decode()
    assert 'https://' not in html
    assert 'http://' not in html
    assert client.get('/static/app.css').status_code == 200
    assert client.get('/static/app.js').status_code == 200
