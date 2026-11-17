"""Exercise the Windows launcher entry point over HTTP with disposable data."""
from contextlib import closing, contextmanager
from datetime import date, timedelta
from http.cookiejar import CookieJar
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, build_opener


PAGES = ('dashboard', 'accounts', 'transactions', 'budgets', 'savings',
         'loans', 'inventory', 'sales', 'reports', 'settings')


def test_waitress_start_pages_and_restart_persistence(tmp_path):
    # Always override the data root: never open the user's personal/demo database.
    data_root = tmp_path / 'isolated-user-data'
    env = dict(os.environ, MYMONEYHUB_DATA_DIR=str(data_root), PYTHONUNBUFFERED='1')
    project = Path(__file__).resolve().parents[1]
    log = tmp_path / 'server.log'
    with closing(socket.socket()) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'

    @contextmanager
    def running_server():
        with log.open('ab') as output:
            process = subprocess.Popen(
                [sys.executable, 'run.py', '--demo', '--no-browser', '--port', str(port)],
                cwd=project, env=env, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            try:
                client = build_opener(HTTPCookieProcessor(CookieJar()))
                deadline = time.monotonic() + 20
                while True:
                    assert process.poll() is None, log.read_text(encoding='utf-8', errors='replace')
                    try:
                        with client.open(base + '/login', timeout=1) as response:
                            assert response.status == 200
                            login = response.read().decode('utf-8')
                        break
                    except (URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise AssertionError('Server did not start: ' + log.read_text(encoding='utf-8', errors='replace'))
                        time.sleep(.1)
                csrf = re.search(r'name="csrf" value="([^"]+)"', login).group(1)
                fields = urlencode({'csrf': csrf, 'password': 'ExploreMyMoney!2026'}).encode()
                with client.open(base + '/login', data=fields, timeout=5) as response:
                    assert response.status == 200
                    assert response.url.endswith('/dashboard')
                yield client
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def get(client, path):
        with client.open(base + path, timeout=5) as response:
            assert response.status == 200
            return response.read()

    def snapshot():
        database = data_root / 'demo' / 'moneyhub.db'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            return '\n'.join(conn.iterdump())

    with running_server() as client:
        for page in PAGES:
            assert b'DEMO DATABASE' in get(client, '/' + page)
        assert get(client, '/static/app.css')
        assert get(client, '/static/app.js')
        html = get(client, '/transactions').decode('utf-8')
        csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        fields = urlencode(dict(csrf=csrf, key='restart-smoke-income', section='transactions',
                                account_id=1, category_id=1, amount='12.34',
                                date=date.today().isoformat(), note='Restart persistence smoke test')).encode()
        with client.open(base + '/action/transaction', data=fields, timeout=5) as response:
            assert b'Saved successfully.' in response.read()
        ledger = get(client, '/export/ledger.csv')
        assert b'Restart persistence smoke test' in ledger
        for action, values in [
            ('loan_plan', dict(key='server-agreement', person='Test borrower', purpose='Emergency repairs',
                               amount='100',rate='20',date=date.today().isoformat(),
                               due=(date.today()+timedelta(days=30)).isoformat())),
            ('payout', dict(key='server-payout',contract_id=3,date=date.today().isoformat())),
            ('repayment',dict(key='server-repayment',loan_id=3,amount='20',date=date.today().isoformat(),reference='HTTP-PAYMENT')),
        ]:
            data = urlencode(dict(csrf=csrf,section='loans',**values)).encode()
            with client.open(base+'/action/'+action,data=data,timeout=5) as response:
                assert b'Saved successfully.' in response.read()
        receipt = get(client,'/loans/3/receipt')
        assert b'OSI-0003' in receipt and b'Emergency repairs' in receipt and b'Given Out' in receipt
        assert b'Remaining loan balance' in get(client,'/repayments/3/receipt')
        ledger = get(client, '/export/ledger.csv')
        before = snapshot()

    # A new OS process opens the same test database and authenticates afresh.
    with running_server() as client:
        for page in PAGES:
            assert b'DEMO DATABASE' in get(client, '/' + page)
        assert get(client, '/export/ledger.csv') == ledger
        assert b'Restart persistence smoke test' in get(client, '/transactions')
        # A new sign-in rotates CSRF tokens in the surrounding layout.
        # The saved receipt itself must remain unchanged.
        article = lambda html: re.search(rb'<article class="receipt".*?</article>',html,re.S).group(0)
        assert article(get(client,'/loans/3/receipt')) == article(receipt)
        assert b'Remaining loan balance' in get(client,'/repayments/3/receipt')
        assert snapshot() == before
    assert not (data_root / 'moneyhub.db').exists()
def test_default_data_folder_is_separate_for_public_release(monkeypatch, tmp_path):
    from moneyhub.db import data_dir

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('MYMONEYHUB_DATA_DIR', raising=False)
    assert data_dir() == tmp_path / 'MyMoneyHub-Public'
    assert data_dir(demo=True) == tmp_path / 'MyMoneyHub-Public' / 'demo'
