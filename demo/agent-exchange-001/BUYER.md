# Owner-operated buyer review

This adds a small local permissions CLI to the existing Exchange preview.
It uses the original Wallet, commission, receiver and simulated settlement
implementations. No external calls, real funds, browser signing or new key
custody platform. The operator still holds all roles' keys, as in the original
preview. It is not safe multi-user authentication or a production wallet.

From `demo/agent-exchange-001`, with Wallet installed in your Python environment:

```bash
python -m exchange.buyer --home /path/to/exchange review \
  --listing <listing-id> --input /path/to/public-input.txt \
  --max-budget 60 --hours 1 --out /tmp/buyer-review.json
python -m exchange.buyer --home /path/to/exchange authorize \
  --review /tmp/buyer-review.json --approve-sha256 <printed-review-digest>
python -m exchange.buyer --home /path/to/exchange status --agent <printed-agent-identity>
python -m exchange.buyer --home /path/to/exchange complete --job <printed-job-id>
python -m exchange.buyer --home /path/to/exchange evidence \
  --job <printed-job-id> --out /tmp/selected-evidence.json
python -m exchange.buyer --home /path/to/exchange revoke --agent <printed-agent-identity>
python -m exchange.buyer --home /path/to/exchange revoke-worker --listing <listing-id>
```

Use an existing initialized Exchange home; `run_demo.py --home <fresh-path>`
creates one. The input starts with `{"nonce":"globally-unique-id"}` on its
first line. The remaining lines are the digest task. Use public fixture
content only. The review file stays local and contains an input path and hash.
The exported evidence does not contain the path, content, private keys, or
Wallet history. Never export the complete Exchange home: it holds private keys.

The review binds worker, offer, input hash, price, buyer's ceiling and expiry.
Authorization refuses changed reviews, changed input/offer, inactive listings,
invalid expiry and over-budget work before delegation. A fresh local agent
receives only the agreed price (even if the ceiling is higher), and the
existing signed agreement freezes the exact worker, job input and payee.
Repeating the same approval recovers its original job rather than delegating
again. Revocation blocks new commissions; earned obligations follow the
existing settlement rules and are not erased.

The review digest is explicit local confirmation, not a remotely authenticated
owner signature. Exact-worker/input selection is checked by this owner-operated
CLI; the underlying general `commission:work` mandate remains the existing
budget/expiry grant. Anyone holding that agent key and bypassing this CLI still
has the original commission contract's capabilities (including reuse of a
released allowance). This interface introduces no stronger capability claim.
`revoke` withdraws the dedicated buyer-agent's Wallet mandate;
`revoke-worker` withdraws the seller's local registry listing, so the existing
kernel refuses future selection and commissioning. These are separate controls.

Selected evidence preserves the original signed offer/agreement/result/verdict/
settlement bodies and hashes. It covers only selected `text_digest` jobs.
Buyer-controlled verification is independent of the seller role; all roles
remain controlled locally. Selection is data minimization, not cryptographic
selective disclosure. It does not prove the absence of omitted jobs, current
mandate standing, or real payment. Export only approved public records.

The complete Bureau integration and controlled allocation walkthrough are in
the paired `openline-bureau` feature branch's `BUREAU_ALLOCATION_001.md`.
