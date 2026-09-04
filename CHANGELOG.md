# Changelog

## 0.1.0 — 2026-09-04

- Replaces the legacy browser viewer as the current product surface.
- Adds a local-first Python CLI for identity, mandates, history, narrowing, revocation, export, import, and evidence verification.
- Adds a separate receiver-owned reference Gate with pinned roots, fresh monotonic bundle admission, exact-action holder proofs, one-use challenges, and signed decision receipts.
- Adds `PLATFORM-EXIT-001` as the executable deterministic acceptance demo.
- Adds the MCP transport/receiver path used by `PLATFORM-EXIT-LIVE-001`.
- Freezes the successful real-host Claude -> revoked Claude -> Codex result from GitHub Actions run `33841050124`.
- Records the earned claim boundary and preserves the non-secret evidence under `proofs/platform-exit-live-001/`.
- Adds adversarial tests and GitHub Actions CI for Python 3.11–3.13.
