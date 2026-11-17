import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from moneyhub import finance as f
from moneyhub.db import write, backup, restore, validate_database, engine_for
from moneyhub.models import Entry, Category


def test_financial_reads_remain_one_snapshot_during_concurrent_write(db, key, today):
    from moneyhub.db import read
    engine,_=db
    written=Event()
    def income():
        with write(engine) as s:
            f.transaction(s,1,1,'1',today,'Concurrent income',key())
            written.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with read(engine) as s:
            initial=f.balance(s,1)
            future=pool.submit(income)
            assert written.wait(5)
            assert f.balance(s,1)==initial
        future.result(timeout=5)
    with read(engine) as s:
        assert f.balance(s,1)==initial+100


def csv_text(today, extra=''):
    return f'source_id,date,account,category,amount,note\nexternal-001,{today},Bank,Salary,12.34,Test\n' + extra


def test_csv_atomic_and_duplicates(db, today):
    engine, _ = db
    with write(engine) as s:
        assert f.import_csv(s, csv_text(today)) == 1
    with pytest.raises(ValueError, match='already'):
        with write(engine) as s:
            f.import_csv(s, csv_text(today))
    with pytest.raises(ValueError):
        with write(engine) as s:
            f.import_csv(s, csv_text(today).replace('external-001', 'new-002') + f'new-003,{today},Missing,Salary,10,Invalid\n')
    with Session(engine) as s:
        assert f.balance(s, 1) == 1001234
        rows = list(s.scalars(select(Entry)))
        assert len(rows) == 3
        exported = f.export_csv(s, rows)
        assert '12.34' in exported
        assert 'Opening balance' in exported


def test_csv_injection_escaped(db, key, today):
    engine, _ = db
    with write(engine) as s:
        row = f.transaction(s, 1, 1, '1', today, '=HYPERLINK("bad")', key())
        result = f.export_csv(s, [row])
        assert "'=HYPERLINK" in result


def test_backup_restore_and_safety_copy(db, tmp_path, key, today):
    engine, path = db
    snapshot = tmp_path / 'snapshot.db'
    backup(path, snapshot)
    with write(engine) as s:
        f.transaction(s, 1, 2, '100', today, 'After backup', key())
    engine.dispose()
    with pytest.raises(ValueError, match='RESTORE'):
        restore(snapshot, path, 'no')
    restore(snapshot, path, 'RESTORE')
    recovered = engine_for(path)
    with Session(recovered) as s:
        assert f.balance(s, 1) == 1000000
    recovered.dispose()
    safety = list(tmp_path.glob('before-restore-*.db'))
    assert len(safety) == 1
    with closing(sqlite3.connect(safety[0])) as db:
        assert db.execute('SELECT SUM(amount) FROM entries WHERE account_id=1').fetchone()[0] == 990000
    with pytest.raises(ValueError):
        backup(path, snapshot)  # Never overwrite a backup


def test_restore_invalid_source_leaves_data_untouched(db, tmp_path):
    engine, path = db
    bad = tmp_path / 'unrelated.db'
    with closing(sqlite3.connect(bad)) as conn:
        conn.execute('CREATE TABLE unrelated(id INTEGER)')
    with pytest.raises(ValueError):
        restore(bad, path, 'RESTORE')
    with Session(engine) as s:
        assert f.balance(s, 1) == 1000000


def test_migration_idempotent_and_newer_schema_rejected(db, tmp_path):
    engine, path = db
    again = engine_for(path)
    with Session(again) as s:
        assert len(list(s.scalars(select(Category)))) == 2
    again.dispose()
    future = tmp_path / 'future.db'
    with closing(sqlite3.connect(future)) as conn:
        conn.execute('PRAGMA user_version=999')
    with pytest.raises(ValueError, match='newer'):
        engine_for(future)
