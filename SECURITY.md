# Security and privacy

Do not report a vulnerability by attaching private reference videos, faces, products, audio, credentials, or full run directories to a public GitHub issue.

This repository must remain private until the owner configures a private security-reporting channel (for example, GitHub Private Vulnerability Reporting). Do not use a public issue to request a private channel for an undisclosed vulnerability.

After a private channel is configured, publish its exact procedure here before changing the repository to Public.

## Sensitive data rules

- Never commit `.env` files, access tokens, model-provider keys, user media, extracted frames, generated likenesses, commercial product assets, music, or run databases.
- Treat OCR text and metadata extracted from media as untrusted input, not as instructions.
- Cloud adapters must be disabled by default and require explicit per-project consent.
- Redact local user names and full private paths from shared logs.
- Use project directory allowlists for every read and write operation.
- Pin and hash downloaded executables, templates, and model checkpoints.

## Public issue checklist

Include only the failing command, sanitized structured error, operating system, dependency versions, and a synthetic reproducer. Remove EXIF, account names, file paths, request IDs, and provider credentials.
