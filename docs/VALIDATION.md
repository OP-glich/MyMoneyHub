# Public Release Validation and Privacy Audit

**Release candidate:** MyMoney Hub 1.0.0  
**Validated:** 2026-11-17  
**Environment:** Windows 11, Python 3.14, SQLite, Flask development source served through the production Waitress launcher on `127.0.0.1`; isolated release-copy virtual environment and temporary data directories.

## Automated checks

| Check | Result |
|---|---|
| Full pytest suite | **92 passed; 21 subtests passed** in 80.01 seconds |
| Standard-library test suite | **21 passed** in 0.263 seconds |
| JavaScript syntax and frontend regression checks | **Passed** (`node --check`; print, clipboard, fallback and disclosure checks) |
| Python source compilation | **Passed** (`compileall` for application, CLI and tests) |
| Dependency consistency (`pip check`) | Passed in the isolated release environment |

All automated tests use temporary databases and synthetic records. The full-suite counts and timings above are from the final candidate run.

## User-visible launcher and persistence workflow

Using the actual `Start Demo.cmd` launcher, pointed at a disposable directory under `.test-runs`:

- The demo server started on `127.0.0.1:8766`; no public/LAN bind was used.
- Login succeeded through the rendered login form and its CSRF token.
- Overview, Transactions, Budgets, Savings, Loans, Loan Records, Sales, Reports and Settings each returned HTTP 200. The Loans page showed the fictional borrower `Demo Borrower A`.
- Submitted a synthetic N$12.34 income through the Transactions form. It appeared in transaction history and updated Overview totals (monthly received N$25,117.34; actual cash N$36,171.54).
- Stopped and restarted the launcher with the same temporary demo data directory. Login, the saved transaction, both Overview totals, and exactly one matching SQLite entry remained present.
- A failed CSRF submission returned HTTP 400 and did not create a second entry.

These workflow records are disposable demo data, not personal financial information.

## Fresh-user startup check

Using `Start MyMoney Hub.cmd` with `LOCALAPPDATA` redirected to a disposable profile and no custom data override:

- The server bound locally and the login/setup page returned HTTP 200 with the first-password form.
- A new `%LOCALAPPDATA%\MyMoneyHub-Public\moneyhub.db` was created, passed SQLite `integrity_check`, and contained zero owners and zero entries.
- The isolated startup did not use the existing application data directory.
## Demo screenshots

`images/demo-overview.png` and `images/demo-loans.png` were captured in local Microsoft Edge from the app running against its isolated, fictional seeded demo database. The screenshots were visually reviewed. They show the app's demo banner, synthetic totals, and fictional records only. No screenshot from the personal application was used.

## Privacy and repository audit

The release folder was created from an allowlisted source/documentation copy. Review covers source, templates, static assets, docs, tests, fixtures, launchers, workflow configuration, screenshot pixels and repository contents.

- No personal database, SQLite sidecar, database backup, borrower record, real receipt, financial export, log, local secret or virtual environment is intended for publication.
- Demo records and screenshots are synthetic. The public receipt identity defaults to `Local Lender`; no personal lender name is hardcoded into receipt output.
- `.gitignore` excludes SQLite files and sidecars, backups, runtime data, local credentials, generated receipts/reports/exports, logs, caches, virtual environments and temporary test runs. Demo images are the only reviewed image exception.
- The original working project has no Git history to carry forward. The public repository is initialized from the clean release folder only.
- No software license is included because no license granting reuse rights was selected.
- The release default is `%LOCALAPPDATA%\MyMoneyHub-Public`, separate from other MyMoney Hub data directories. The real application database and its backups were not copied, migrated, reset or written by these release checks. All tests and the launcher workflow used isolated temporary paths.

## Remaining scope

This validates the release copy and its local workflows. No GitHub repository has been created or published from this environment; no source or data has been uploaded. The connected GitHub integration can inspect repositories but does not provide repository-creation/push operations. Publish the verified local folder through GitHub Desktop after reviewing this audit.