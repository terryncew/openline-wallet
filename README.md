# OpenLine Wallet · Portable Proof for AI (OLR/1.5)

**Verify tiny, signed AI receipts — offline, in your browser.**  
No prompts, no PII, no vendor lock-in. Sub-1KB receipts with policy-pinned badges (GREEN/AMBER/RED) and three quantized diagnostic dials for AMBER/RED runs.

-----

## What this is

OpenLine Wallet is a **static, zero-dependency** viewer/validator for **OpenLine Receipts (OLR)**:

- **Small:** GREEN ≤ 640 B; AMBER/RED ≤ 800 B
- **Attestable:** optional **ed25519** signature and **policy hash** pinning
- **Actionable:** AMBER/RED receipts carry three 1-byte dials (q8) — ∂Φ/∂κ, d²Φ/dt², freshness
- **Portable:** works with **any** OLR/1.5 receipt served over HTTPS or from local files

> Goal: **audit & triage**, not full tracing. “Proof moves; data doesn’t.”

-----

## Live demo

- Wallet (GitHub Pages): `https://terryncew.github.io/openline-wallet/`
- Load any public receipt: append `?u=<ENCODED_RECEIPT_URL>`  
  Example:  
  `https://terryncew.github.io/openline-wallet/?u=https%3A%2F%2Fraw.githubusercontent.com%2Fterryncew%2Fopen-receipts%2Fmain%2Fdocs%2Ftest-vectors%2Folr15_amber.json`

> You can also drag a local JSON file into the page or run the wallet from a local `file://` URL.

-----

## Quickstart (60 seconds)

**Option A — Use it hosted**

1. Open the Wallet page.
1. Click **Load** → paste a receipt URL, or use the `?u=` parameter.
1. (Optional) Add `issuer.pub.json` to enable “Verify signature” (see below).

**Option B — Self-host (static)**

1. Fork or download this repo.
1. Put your receipt(s) in `docs/` (e.g., `docs/receipt.latest.json`).
1. Enable GitHub Pages → “Deploy from `docs/`”.

**Option C — Embed in an app**

- Include `index.html` and `verify.js` in your static bundle.
- Provide a URL (or file blob) that returns a valid OLR JSON object.

-----

## Minimal receipt (OLR/1.5)

```json
{
  "receipt_version": "olr/1.5",
  "attrs": { "status": "amber", "ts": "2025-10-01T12:00:00Z", "run_id": "demo-001" },
  "policy": {
    "policy_id": "openline/policy.v1",
    "policy_hash": "sha256:6babccff24555fa448e5d0dcfd9c1404cd1d1e8c6ca7c151bc675fd7235f8ed6"
  },
  "signals": { "phi_star": 0.68, "kappa": 0.82, "dhol": 0.22 },
  "telem": {
    "dials": {
      "dphi_dk_q8": 148,
      "d2phi_dt2_q8": 112,
      "fresh_ratio_q8": 164,
      "scale": "q8_signed"
    }
  }
}
```

- **GREEN** receipts omit `telem.dials` entirely to keep size minimal.
- Dials appear only on **AMBER/RED**.

-----

## Signature verification (optional)

Add the issuer’s public key to `docs/issuer.pub.json`:

```json
{
  "issuer": "did:web:openreceipts.dev",
  "ed25519_public_key_hex": "0x0123ab..."
}
```

When a receipt includes a `sig` field, the Wallet verifies a JCS-ish canonical JSON (sorted keys) against this key. If `issuer.pub.json` is absent, you can still inspect receipts without signature checks.

COSE_Sign1 envelopes are on the roadmap; for now, hex-encoded ed25519 signatures are supported directly.

-----

## Policy pinning

Receipts carry a `policy_id` and a content hash of the policy in force (e.g., `sha256:6bab...f8ed6`). That hash is computed from a canonical form (line-ending normalized, `policy_hash:` line removed) to make audits reproducible across platforms.

- Change the policy → the hash changes → downstream can tell which thresholds were active for this run.

-----

## Dials (q8) — what they mean

|Dial (q8)       |Meaning (decoded)                        |Why it helps                        |
|----------------|-----------------------------------------|------------------------------------|
|`dphi_dk_q8`    |∂Φ*/∂κ (gradient of coherence wrt stress)|Is tightening helping or hurting?   |
|`d2phi_dt2_q8`  |d²Φ*/dt² (curvature over time)           |Early warning before snap-lines     |
|`fresh_ratio_q8`|Spectral novelty / mode diversity proxy  |Detects collapse into stale patterns|

q8 decoding maps [-1 … +1] → [0 … 255] with ~128 ≈ 0. The Wallet displays decoded floats for readability.

-----

## Privacy & security boundaries

- No prompts/content are stored or displayed — only small numeric signals and badges.
- Receipts are static files you control. Verification runs locally.
- Keep secrets out of receipts; treat signatures and keys as public material.

-----

## Compatibility

- **Spec:** OLR/1.5 (backward-compatible with 1.4L; older receipts render without dials)
- **Browsers:** Latest Chrome, Safari, Firefox, Edge
- **Hosting:** Any static server / GitHub Pages

-----

## Roadmap

- COSE_Sign1 envelopes (binary-compact signatures)
- Merkle rollups for history attestation
- Multi-issuer key registry & revocation hints
- Accessibility and small-screen UX polish

-----

## Cite this work

If you use OpenLine Wallet/Receipts in research or production write-ups, please cite:

- White, T. Coherence Dynamics Framework. Zenodo (2025). DOI: 10.5281/zenodo.17476985
- White, T. OpenLine Wallet / Open Receipts (Artifacts & Test Vectors). Zenodo (2025). DOI: 10.5281/zenodo.17520176

### BibTeX

```bibtex
@misc{white_coherence_dynamics_2025,
  author = {White, Terrynce},
  title  = {Coherence Dynamics Framework},
  year   = {2025},
  doi    = {10.5281/zenodo.17476985}
}
@misc{white_openline_wallet_2025,
  author = {White, Terrynce},
  title  = {OpenLine Wallet / Open Receipts (Artifacts \& Test Vectors)},
  year   = {2025},
  doi    = {10.5281/zenodo.17520176}
}
```

-----

## License

MIT — see LICENSE.

Questions / pilots: open an issue or DM @sir_terrynce.
