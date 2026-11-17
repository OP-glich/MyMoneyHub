CREATE TABLE receipt_deletions (
 id INTEGER PRIMARY KEY,
 contract_id INTEGER UNIQUE REFERENCES loan_contracts(id),
 repayment_id INTEGER UNIQUE REFERENCES repayments(id),
 deleted_at VARCHAR(40) NOT NULL,
 CHECK ((contract_id IS NOT NULL AND repayment_id IS NULL) OR (contract_id IS NULL AND repayment_id IS NOT NULL))
);
