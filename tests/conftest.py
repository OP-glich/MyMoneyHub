import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from moneyhub.db import engine_for, write
from moneyhub.models import Category, Product
from moneyhub import finance as f


@pytest.fixture
def key():
    return lambda: uuid.uuid4().hex


@pytest.fixture
def today():
    return date.today().isoformat()


@pytest.fixture
def db(tmp_path, key, today):
    path = tmp_path / 'test.db'
    engine = engine_for(path)
    with write(engine) as s:
        a = f.create_account(s, 'Bank', '10000.00', today, key())
        b = f.create_account(s, 'Wallet', '500.00', today, key())
        s.add_all([Category(name='Salary', kind='income'), Category(name='Food', kind='expense'), Product(name='Notebooks')])
    yield engine, path
    engine.dispose()
