import os
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

SCHEMA_VERSION = 4
MIGRATION_DIR = Path(__file__).parent / 'migrations'


def data_dir(demo=False):
    root = Path(os.environ.get('MYMONEYHUB_DATA_DIR') or
                Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'MyMoneyHub-Public')
    return root / 'demo' if demo else root


def engine_for(path):
    from sqlalchemy import create_engine, event
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine('sqlite:///' + path.as_posix(), connect_args={'timeout': 20})

    @event.listens_for(engine, 'connect')
    def configure(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=20000')
    try:
        migrate(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def migrate(engine):
    """Versioned migration 001: transactional initial normalized schema."""
    conn = engine.raw_connection()
    try:
        migrate_connection(conn)
    finally:
        conn.close()


def migrate_connection(conn, target_version=SCHEMA_VERSION, make_backup=True):
    """DB-API migration runner, also runnable without SQLAlchemy for recovery."""
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > target_version:
        raise ValueError('Database is newer than this application.')
    if make_backup and 0 < version < target_version:
        from datetime import datetime
        filename = conn.execute('PRAGMA database_list').fetchone()[2]
        if filename:
            source = Path(filename)
            destination = source.parent / 'backups' / ('before-upgrade-v' + str(version) + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
            # Verified snapshot must complete before any schema writes begin.
            backup(source, destination)
    conn.execute('BEGIN IMMEDIATE')
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version > target_version:
            raise ValueError('Database is newer than this application.')
        if version == 0:
            sql = (MIGRATION_DIR / '001_initial.sql').read_text(encoding='utf-8')
            for statement in sql.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=1')
            version = 1
        if version == 1 and target_version >= 2:
            sql = (MIGRATION_DIR / '002_everyday.sql').read_text(encoding='utf-8')
            for statement in sql.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=2')
            version = 2
        if version == 2 and target_version >= 3:
            sql = (MIGRATION_DIR / '003_entry_revisions.sql').read_text(encoding='utf-8')
            for statement in sql.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=3')
            version = 3
        if version == 3 and target_version >= 4:
            sql = (MIGRATION_DIR / '004_receipt_deletions.sql').read_text(encoding='utf-8')
            for statement in sql.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=4')
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def read(engine):
    """One SQLite snapshot for a complete financial page/receipt.

    Python's legacy SQLite transaction mode doesn't BEGIN on SELECT. Explicit
    BEGIN prevents a concurrent payout/payment from splitting a multi-query view.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        yield session


@contextmanager
def write(engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    with Session(engine, expire_on_commit=False) as session:
        session.execute(text('BEGIN IMMEDIATE'))
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def validate_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup integrity check failed.')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Backup has broken references.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if not 1 <= version <= SCHEMA_VERSION:
            raise ValueError('Backup schema version is not supported.')
        reference = sqlite3.connect(':memory:')
        try:
            migrate_connection(reference, target_version=version, make_backup=False)
            expected = {r[0] for r in reference.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            signatures = {name: reference.execute(f'PRAGMA table_info("{name}")').fetchall() for name in expected}
            foreign_keys = {name: reference.execute(f'PRAGMA foreign_key_list("{name}")').fetchall() for name in expected}
        finally:
            reference.close()
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not expected <= actual:
            raise ValueError('Not a MyMoney Hub backup.')
        for name in expected:
            if db.execute(f'PRAGMA table_info("{name}")').fetchall() != signatures[name]:
                raise ValueError('Backup schema is incomplete.')
            if db.execute(f'PRAGMA foreign_key_list("{name}")').fetchall() != foreign_keys[name]:
                raise ValueError('Backup reference schema does not match.')


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Choose a new backup filename.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    validate_database(destination)
    return destination


def restore(source, destination, confirmation):
    """Offline only. Existing data is preserved in a uniquely named safety copy."""
    from datetime import datetime
    if confirmation != 'RESTORE':
        raise ValueError('Type RESTORE to confirm replacement of current data.')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError('Source and destination must differ.')
    validate_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup(destination, destination.with_name('before-restore-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db'))
    # SQLite backup API also handles existing database pages safely.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    validate_database(destination)
