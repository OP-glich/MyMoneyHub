# Architecture

Flask serves Jinja2 templates and bundled JavaScript/CSS through Waitress on
127.0.0.1. SQLite lives in a persistent Windows user-data directory outside the
project. SQLAlchemy maps normalized accounts, ledger entries, categories,
budgets, savings reservations, loan contracts, issued loans, repayments,
inventory lots, FIFO allocations and sales.

All money is integer minor units, parsed and rounded with Decimal. The shared
`summaries.snapshot` read model supplies dashboard and module totals. Explicit
read transactions give each page one coherent SQLite snapshot. Writes use
BEGIN IMMEDIATE, atomic commit/rollback, unique operation keys and validation.

A loan contract may be a draft with no cash movement. Issuance links one Loan
and signed cash entry to that contract and preflights receipt PDF generation
before committing. Receipt reads never create payouts. Status derives from
verified payout evidence, unpaid principal/interest and due date. Repayments
cover interest first and generate separate receipts whose historical remaining
balance is calculated through that payment.

`loan_records.register` projects saved rows for search/filter/sort and PDF reports.
Lending reports exclude borrowing. Date filters select loan issue/agreement
dates; all saved payments on those loans remain included. The local PDF writer
embeds installed Windows fonts, wraps text and paginates without a service.

Savings reserve cash rather than posting expenses. Transfers are not income.
Stock purchases change cash/inventory; FIFO sales link actual cost allocations
to revenue, preventing double counting of purchases as ordinary spending.

Schema migrations are versioned SQL in `moneyhub/migrations`. Existing versioned
databases are backed up and verified before upgrade. Version 3 adds append-only
ordinary transaction corrections; version 4 adds receipt-deletion audit markers.
Deleting a receipt never deletes the underlying financial event. Direct repayment
correction/reversal is not implemented.

Authentication uses secure Werkzeug password hashes, a local random session
secret, CSRF protection, trusted hosts and localhost-only serving. This is one
local owner per data directory; it is not a network multi-tenant service.
