# Security

OpenLine Wallet v0.1 is a reference implementation. Do not use its local plaintext key files for production funds, infrastructure, medical systems, or physical actuators.

## Invariants

- Export bundles never contain private keys.
- A principal root certifies the wallet epoch key.
- The epoch key may issue and strictly narrow mandates.
- Root-signed revocation removes standing without changing the original mandate bytes.
- A receiver pins the root outside the bundle, admits only fresh monotonic history, and owns every consequential decision.
- Holder presentations bind a subject key to one receiver challenge and one exact action.
- Competing valid heads quarantine the principal instead of letting the Gate invent consensus.

## Known limits

- Local key files are permission-restricted, not encrypted or hardware-backed.
- Fresh exports have a hard 600-second ceiling. Revocation exposure before delivery remains possible inside that window.
- Root recovery, guardian custody, witness distribution, and cross-device sync are absent from the v0.1 product.
- The reference Gate is in-memory. Production integrations must durably commit admitted heads, fork quarantine, and one-use challenge consumption before effects.
- The v0.1 CLI has one certified wallet epoch; it does not yet rotate epochs.
- Receiver root onboarding and human identity proof are deployment responsibilities.
- Export bundles expose the authority history they contain.

## Reporting

Do not open a public issue for a suspected key leak or exploitable verification bypass. Use GitHub's private vulnerability reporting when enabled, or contact the repository owner privately.
