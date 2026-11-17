"""Recovery/money checks runnable even before third-party dependencies install."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from moneyhub.amounts import money, nad, MAX_CENTS, term_interest, split_payment, fifo_plan
from moneyhub.db import migrate_connection, backup, restore, validate_database, SCHEMA_VERSION


class MoneyTests(unittest.TestCase):
    def test_exact_minor_units(self):
        for value, expected in [('0.01', 1), ('1234.56', 123456), ('-1.23', -123),
                                (Decimal('1.10'), 110), ('0', 0)]:
            with self.subTest(value=value):
                self.assertEqual(money(value), expected)
        self.assertEqual(money('0.10') + money('0.20'), 30)

    def test_invalid_values(self):
        for value in ['NaN', 'Infinity', '-Infinity', '0.001', 'abc', None, '1000000000000.01', 0.1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_positive_validation(self):
        for value in ['0', '-1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value, positive=True)
        self.assertEqual(money('0.01', positive=True), 1)

    def test_display(self):
        self.assertEqual(nad(123456), 'N$ 1,234.56')
        self.assertEqual(nad(-101), 'N$ -1.01')
        self.assertEqual(nad(0), 'N$ 0.00')
        self.assertEqual(money('1000000000000.00'), MAX_CENTS)


class FinanceCalculationTests(unittest.TestCase):
    def test_simple_interest(self):
        self.assertEqual(term_interest(100000, 500), 5000)
        self.assertEqual(term_interest(100000, 0), 0)

    def test_interest_rounds_half_up_once(self):
        self.assertEqual(term_interest(101, 50), 1)
        self.assertEqual(term_interest(100, 50), 1)
        self.assertEqual(term_interest(99, 50), 0)

    def test_partial_interest_first(self):
        self.assertEqual(split_payment(5000, 100000, 10000), (0, 5000))
        self.assertEqual(split_payment(15000, 100000, 10000), (5000, 10000))
        self.assertEqual(split_payment(95000, 95000, 0), (95000, 0))

    def test_overpayment_and_invalid_payment(self):
        for payment in [0, -1, 110001]:
            with self.subTest(payment=payment), self.assertRaises(ValueError):
                split_payment(payment, 100000, 10000)

    def test_fifo_sale_profit(self):
        allocations = fifo_plan([(1, 3, 1010), (2, 2, 1220)], 4)
        self.assertEqual(allocations, [(1, 3, 3030), (2, 1, 1220)])
        cost = sum(item[2] for item in allocations)
        self.assertEqual(4*2000-cost, 3750)

    def test_fifo_exact_stock_and_empty_lots(self):
        self.assertEqual(fifo_plan([(1, 0, 100), (2, 2, 110)], 2), [(2, 2, 220)])
        for lots, quantity in [([], 1), ([(1, 1, 100)], 2), ([(1, 1, 100)], 0)]:
            with self.subTest(lots=lots, quantity=quantity), self.assertRaises(ValueError):
                fifo_plan(lots, quantity)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/'moneyhub.db'
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('PRAGMA foreign_keys=ON')
        migrate_connection(self.conn)
        self.conn.execute("INSERT INTO accounts(id,name) VALUES (1,'Bank')")
        self.conn.execute("INSERT INTO operations(id,kind) VALUES ('open','account')")
        self.conn.execute("INSERT INTO entries(account_id,operation_id,date,kind,amount,note) VALUES (1,'open','2026-11-01','opening',100000,'Opening balance')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_migration_is_idempotent_and_persists(self):
        migrate_connection(self.conn)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        with closing(sqlite3.connect(self.path)) as second:
            self.assertEqual(second.execute('SELECT SUM(amount) FROM entries').fetchone()[0], 100000)
        validate_database(self.path)

    def test_newer_version_rejected_without_changes(self):
        self.conn.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            migrate_connection(self.conn)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 99)
        self.assertEqual(self.conn.execute('SELECT SUM(amount) FROM entries').fetchone()[0], 100000)

    def test_migration_rollback(self):
        fresh = sqlite3.connect(':memory:')
        try:
            fresh.execute('CREATE TABLE accounts(id INTEGER)')
            with self.assertRaises(sqlite3.OperationalError):
                migrate_connection(fresh)
            self.assertEqual(fresh.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertIsNone(fresh.execute("SELECT name FROM sqlite_master WHERE name='owners'").fetchone())
        finally:
            fresh.close()

    def test_constraints_and_atomic_rollback(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("INSERT INTO accounts(name) VALUES ('Wallet')")
                self.conn.execute("INSERT INTO entries(account_id,operation_id,date,kind,amount) VALUES (999,'open','2026-11-01','income',100)")
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("INSERT INTO entries(account_id,operation_id,date,kind,amount) VALUES (1,'open','2026-11-01','income',0)")

    def test_duplicate_import_id_constraint(self):
        self.conn.execute("UPDATE entries SET source_id='bank-id-001'")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("INSERT INTO entries(account_id,operation_id,date,kind,amount,source_id) VALUES (1,'open','2026-11-01','income',10,'bank-id-001')")

    def test_stock_check_constraint(self):
        self.conn.execute("INSERT INTO products(id,name) VALUES (1,'Notebook')")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute('INSERT INTO lots(product_id,entry_id,quantity,remaining,unit_cost) VALUES (1,1,10,-1,100)')

    def test_backup_round_trip_and_safety_copy(self):
        snapshot = self.root/'snapshot.db'
        backup(self.path, snapshot)
        self.conn.execute('UPDATE entries SET amount=90000')
        self.conn.commit()
        restore(snapshot, self.path, 'RESTORE')
        self.assertEqual(self.conn.execute('SELECT SUM(amount) FROM entries').fetchone()[0], 100000)
        safety = list(self.root.glob('before-restore-*.db'))
        self.assertEqual(len(safety), 1)
        with closing(sqlite3.connect(safety[0])) as previous:
            self.assertEqual(previous.execute('SELECT SUM(amount) FROM entries').fetchone()[0], 90000)
        validate_database(self.path)

    def test_restore_requires_confirmation(self):
        snapshot = self.root/'snapshot.db'
        backup(self.path, snapshot)
        with self.assertRaises(ValueError):
            restore(snapshot, self.path, '')
        self.assertFalse(list(self.root.glob('before-restore-*.db')))

    def test_invalid_backup_does_not_replace_current(self):
        bad = self.root/'bad.db'
        with closing(sqlite3.connect(bad)) as conn:
            conn.execute('CREATE TABLE unrelated(id INTEGER)')
        with self.assertRaises(ValueError):
            restore(bad, self.path, 'RESTORE')
        self.assertEqual(self.conn.execute('SELECT SUM(amount) FROM entries').fetchone()[0], 100000)

    def test_backup_never_overwrites(self):
        snapshot = self.root/'snapshot.db'
        backup(self.path, snapshot)
        with self.assertRaises(ValueError):
            backup(self.path, snapshot)
        with self.assertRaises(ValueError):
            backup(self.path, self.path)

    def test_foreign_key_corruption_rejected(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.conn.execute('UPDATE entries SET account_id=999')
        self.conn.commit()
        with self.assertRaises(ValueError):
            validate_database(self.path)


if __name__ == '__main__':
    unittest.main()
