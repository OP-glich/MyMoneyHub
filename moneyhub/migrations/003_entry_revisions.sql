CREATE TABLE entry_revisions (
 id INTEGER PRIMARY KEY,
 old_entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 reversal_entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id),
 replacement_entry_id INTEGER UNIQUE REFERENCES entries(id),
 operation_id VARCHAR(100) NOT NULL UNIQUE REFERENCES operations(id),
 created_at VARCHAR(40) NOT NULL,
 reason VARCHAR(500) NOT NULL
);
INSERT INTO loan_contracts (loan_id,account_id,direction,person,purpose,principal,rate_bps,basis,interest,start,due,terms)
 SELECT l.id,l.account_id,l.direction,l.person,'',l.principal,l.rate_bps,'fixed',l.interest,l.start,l.due,l.terms
 FROM loans l WHERE NOT EXISTS (SELECT 1 FROM loan_contracts c WHERE c.loan_id=l.id);
