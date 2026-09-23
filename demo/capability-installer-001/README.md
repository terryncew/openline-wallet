# capinstall — local receiver-controlled capability installer (developer preview)

A thin adapter over the machinery proven in CAPABILITY-EXCHANGE-001. The
receiver (you, the buyer) decides: inspect a capability package, run your own
acceptance checks, import only the exact package that passed, invoke it
through the receiver boundary, and revoke future use.

## What this is

A local installer for small deterministic Python components. Both parties
run on this host with separate keys: the seller signs the package, the
buyer's wallet holds the invoke mandate. That demonstrates the authority
boundary. It is not outside adoption, not a marketplace, and not a payment
system.

## Quickstart (copyable)

```bash
# one-time local setup: home, buyer wallet, demo keys, example package
capinstall init

PKG=~/.capinstall/packages/example

# 1. inspect the package: identity, version/hash, interface, dependencies,
#    requested permissions, supplied evidence. Nothing is executed.
capinstall inspect $PKG

# 2. run the buyer-selected acceptance checks (default T1-T5, threshold 10/12)
capinstall accept $PKG

# 3. import only the exact package that passed (hash re-verified)
capinstall import $PKG

# 4. invoke through the receiver boundary on fresh work; saves a receipt
PH=$(python3 -c "import json,os; print(list(json.load(open(os.path.expanduser('~/.capinstall/decisions.json'))))[0])")
capinstall invoke $PH w01_mixed_eval_majority

# optional simulated settlement demo (SIM_USD has no real value)
capinstall settle $PH --demo

# 5. revoke future invocation through the receiver
capinstall revoke $PH
capinstall invoke $PH w02_all_ok   # refused: MANDATE_REVOKED
```

The example capability is `symptom_summarizer` (baseline), the exact
artifact traded in the exchange study (sha256 `80d6bb6e…`). Its buyer
battery score is 10/12 against the buyer's declared threshold of 10/12.
That score is the buyer's own acceptance result, not universal correctness.

## Where things live

- installer state: `~/.capinstall/` (override with `CAPINSTALL_HOME`)
- acceptance decisions: `~/.capinstall/decisions.json`
- imported lineage: `~/.capinstall/lineage/<package-hash>/`
- invocation receipts: `~/.capinstall/receipts/invocations/`
- acceptance records: `~/.capinstall/receipts/acceptance_<hash>.json`
- simulated settlement ledger: `~/.capinstall/settlement.json`
- invoke mandates: the buyer wallet at `~/.capinstall/wallet/`

Refusals are readable and coded: `IMPORT_REFUSED`,
`IMPORT_BINDING_MISMATCH`, `INVOCATION_BINDING_MISMATCH`,
`MANDATE_REVOKED`, `ALREADY_SETTLED`, `BATTERY_INTEGRITY`,
`INVOCATION_REQUIRED`, `SETTLEMENT_REFUSED`, `SIGNATURE_MISSING`,
`SIGNATURE_INVALID`, `MANIFEST_TAMPERED`,
`SELLER_PRINCIPAL_MISMATCH`, `NOT_IMPORTED`.

## Supported scope

Small deterministic Python components exposing the demonstrated
interface: one entry point, stdlib only, no network, no filesystem
access beyond their inputs. That is what the acceptance battery can
meaningfully check.

## Limits (read before running anything)

- **Hashes establish identity, not safety.** A matching hash proves the
  package is the one you accepted. It says nothing about what the code
  does.
- **No sandbox.** The capability module is loaded in-process and runs
  with your host's full privileges. Only install packages from sellers
  you trust, exactly as you would only run code from sources you trust.
- **Acceptance is yours, not universal.** The 10/12 score measures the
  capability against your battery and threshold. It is not a claim the
  component is correct in general.
- **Revocation is receiver-scoped.** Revoking blocks future invocation
  through this receiver. It does not erase exported code and does not
  prevent someone from running a copy elsewhere.
- **Settlement is simulated.** `capinstall settle --demo` moves
  SIM_USD in a local ledger, once per package, only after acceptance
  and a verified invocation. No real money moves.
- **Single host.** Seller and buyer keys both live here. Separate keys
  demonstrate the authority boundary; they do not establish independent
  adoption.

## Regression controls

`tests/test_capability_installer.py` runs the discriminating controls
against the CLI entry point in unittest style (the same suite CI runs:
`python -m unittest discover -s tests`): rejected package, unsigned or
tampered-signature package, manifest-field substitution under an old
signature, seller-principal mismatch, substituted artifact, wrong version,
wrong buyer, altered acceptance policy, unsigned or forged invocation
receipt, settlement replay, revoked invocation.
