# Contributing

Discuss focused changes in an issue before introducing new modules or dependencies.
Preserve the calm V2 interface and offline-first behavior.

- Use Python 3.11+ on Windows and a local virtual environment.
- Run the full pytest and standard-library suites; use temporary synthetic databases.
- Keep currency in integer cents or Decimal, never binary floating point.
- Preserve transactional writes, request idempotency and financial audit trails.
- Verify backup/restore and migration behavior when persistence changes.
- Add all frontend assets locally. Do not introduce telemetry or external services.
- Review every staged file and the complete commit history for private data.
- Never include real names, contact details, receipts, databases, secrets or logs.

No project license has been selected. Discuss contribution and reuse terms with
the repository owner before submitting code that grants additional rights.
