CREATE TABLE owners (
 id INTEGER PRIMARY KEY, password_hash VARCHAR NOT NULL
);
CREATE TABLE accounts (
 id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE categories (
 id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
 kind VARCHAR NOT NULL CHECK (kind IN ('income','expense'))
);
CREATE TABLE operations (
 id VARCHAR(100) PRIMARY KEY NOT NULL, kind VARCHAR NOT NULL
);
CREATE TABLE entries (
 id INTEGER PRIMARY KEY,
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 category_id INTEGER REFERENCES categories(id),
 operation_id VARCHAR(100) NOT NULL REFERENCES operations(id),
 date VARCHAR(10) NOT NULL, kind VARCHAR NOT NULL,
 amount INTEGER NOT NULL CHECK (amount != 0),
 note VARCHAR(500) NOT NULL DEFAULT '', source_id VARCHAR(200) UNIQUE
);
CREATE INDEX ix_entries_account_date ON entries(account_id,date);
CREATE INDEX ix_entries_category_date ON entries(category_id,date);
CREATE TABLE budgets (
 id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id),
 month VARCHAR(7) NOT NULL, amount INTEGER NOT NULL CHECK (amount > 0),
 UNIQUE (category_id,month)
);
CREATE TABLE savings (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
 name VARCHAR(100) NOT NULL, amount INTEGER NOT NULL, target INTEGER NOT NULL,
 CHECK (amount >= 0 AND target > 0 AND amount <= target)
);
CREATE TABLE loans (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
 entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 direction VARCHAR NOT NULL CHECK (direction IN ('lend','borrow')),
 person VARCHAR(100) NOT NULL, principal INTEGER NOT NULL,
 rate_bps INTEGER NOT NULL, term_days INTEGER NOT NULL, interest INTEGER NOT NULL,
 start VARCHAR(10) NOT NULL, due VARCHAR(10) NOT NULL, terms VARCHAR(500) NOT NULL,
 CHECK (principal > 0 AND interest >= 0 AND rate_bps >= 0 AND term_days > 0)
);
CREATE TABLE repayments (
 id INTEGER PRIMARY KEY, loan_id INTEGER NOT NULL REFERENCES loans(id),
 entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 reference VARCHAR(100) NOT NULL, principal INTEGER NOT NULL, interest INTEGER NOT NULL,
 UNIQUE (loan_id,reference),
 CHECK (principal >= 0 AND interest >= 0 AND principal + interest > 0)
);
CREATE TABLE products (
 id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE lots (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 quantity INTEGER NOT NULL, remaining INTEGER NOT NULL, unit_cost INTEGER NOT NULL,
 CHECK (quantity > 0 AND remaining >= 0 AND remaining <= quantity AND unit_cost > 0)
);
CREATE TABLE sales (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 quantity INTEGER NOT NULL, revenue INTEGER NOT NULL, cost INTEGER NOT NULL,
 CHECK (quantity > 0 AND revenue > 0 AND cost >= 0)
);
CREATE TABLE allocations (
 id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
 lot_id INTEGER NOT NULL REFERENCES lots(id), quantity INTEGER NOT NULL CHECK (quantity > 0),
 UNIQUE (sale_id,lot_id)
);
