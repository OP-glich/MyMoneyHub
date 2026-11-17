# Security and privacy

This is a localhost desktop application, not a hosted multi-user banking service.
Keep the server bound to 127.0.0.1. Never commit user databases, authentication
secrets, hashes, exports, backups or personal screenshots, even in a private PR.

Data is not encrypted at rest. Anyone with sufficient Windows filesystem access
can read local records. Protect your operating-system account and backup storage.
The owner password controls application access; it does not encrypt SQLite files.

Use isolated synthetic data to reproduce bugs. For a suspected vulnerability,
use GitHub's private security reporting option if available. If unavailable,
open a minimal issue asking for a private contact channel without exploit details
or personal information. Do not post secrets or financial records publicly.

The repository intentionally contains no user database or historical personal
validation reports. Known demo/test passwords apply only to fictional data.
