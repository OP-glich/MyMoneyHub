ALTER TABLE products ADD COLUMN selling_price INTEGER NOT NULL DEFAULT 0 CHECK (selling_price >= 0);
CREATE TABLE preferences (
 id INTEGER PRIMARY KEY CHECK (id = 1),
 account_id INTEGER NOT NULL REFERENCES accounts(id)
);
CREATE TABLE monthly_budgets (
 id INTEGER PRIMARY KEY,
 month VARCHAR(7) NOT NULL UNIQUE,
 amount INTEGER NOT NULL CHECK (amount > 0)
);
CREATE TABLE loan_contracts (
 id INTEGER PRIMARY KEY,
 loan_id INTEGER UNIQUE REFERENCES loans(id),
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 direction VARCHAR NOT NULL CHECK (direction IN ('lend','borrow')),
 person VARCHAR(100) NOT NULL,
 contact VARCHAR(100) NOT NULL DEFAULT '',
 purpose VARCHAR(500) NOT NULL,
 principal INTEGER NOT NULL CHECK (principal > 0),
 rate_bps INTEGER NOT NULL CHECK (rate_bps >= 0),
 basis VARCHAR NOT NULL CHECK (basis IN ('fixed','annual')),
 interest INTEGER NOT NULL CHECK (interest >= 0),
 start VARCHAR(10) NOT NULL,
 due VARCHAR(10) NOT NULL,
 terms VARCHAR(500) NOT NULL DEFAULT ''
);
INSERT INTO loan_contracts (id,loan_id,account_id,direction,person,purpose,principal,rate_bps,basis,interest,start,due,terms)
 SELECT id,id,account_id,direction,person,'',principal,rate_bps,'fixed',interest,start,due,terms FROM loans;
