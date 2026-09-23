# RRSI-to-OpenLine promotion adapter (developer preview)

A promotion adapter for RRSI-style harness candidates, demonstrated with a
synthetic candidate based on a byte-verified RRSI component.

An optimizer may propose a harness change. Only the operator-owned receiver
may install that exact change into the protected runtime.

This is a thin adapter, not a new system. It reuses the merged
capability-installer machinery (package validation, wallet mandates,
revocation) and adds the promotion-specific pieces: git-candidate export with
exact base and artifact hashes, operator-owned protected checks, a guarded
deployment dir, and clean-environment invocation.

## Prerequisite

A checkout of the RRSI source containing the pinned commit:

```bash
export RRSI_REPO="$HOME/workspace/vendor/rrsi"   # or any directory you choose
git clone https://github.com/google-research/rrsi "$RRSI_REPO"
git -C "$RRSI_REPO" checkout e4d1a7a0388e02b388bc40eb0a125fcfc7123f8d
```

The quickstart fails clearly if the pinned commit is not available at
`$RRSI_REPO`. The RRSI clone is read-only outside apparatus; the demo never
writes to it.

## Run it

```bash
cd demo/rrsi-promotion-adapter
bash quickstart.sh            # full demo, ~3s, zero paid calls
bash quickstart.sh /tmp/demo  # work dir of your choice
```

The quickstart builds a synthetic candidate repo, exports a candidate, runs
the operator checks, installs, invokes on fresh local work, then demonstrates
four blocked bypasses (changed base, rejected candidate, non-operator caller
on the guarded promotion path, substituted artifact) and revocation.

## What it demonstrates

1. Export a candidate with exact base hash and resulting artifact hash.
2. Evaluate it against operator-owned protected checks, after verifying the
   candidate signer is the operator-pinned optimizer identity.
3. Refuse installation on rejection or a changed base.
4. Install only the exact accepted artifact.
5. Invoke it on fresh local work and record the loaded digest.
6. Refuse substitution and refuse non-operator callers on the guarded
   promotion path.
7. Revoke; the next gated invocation is refused.

## Scope and limits

- One small harness component only:
  `third_party/harbor_terminus2/terminus_json_plain_parser.py` from
  google-research/rrsi at pinned commit
  `e4d1a7a0388e02b388bc40eb0a125fcfc7123f8d`. The adapter does not promote
  whole harnesses.
- The candidates are synthetic integration fixtures, clearly labeled. No
  preserved upstream RRSI candidate exists in the public repo, and nothing
  here claims RRSI generated or discovered an improvement.
- Signer trust: acceptance requires the candidate signer to match the
  optimizer identity the operator pinned at init (`optimizer.pub.json`). A
  different valid signing key is refused before the operator's checks run.
  In this preview the operator generates the optimizer key at init as a
  stand-in for the external optimizer.
- The receiver-managed install path grants no promotion authority to the
  optimizer; this single-host preview does not prevent same-user filesystem
  writes. Only the operator identity may install through the guarded path.
- The subprocess receives no wallet/key paths or receiver credentials
  through its environment. It is not sandboxed and retains the host user's
  filesystem privileges. Hashes identify the artifact; they do not certify
  it safe.
- Proof ceiling: operator-controlled promotion flow, not adversarial
  isolation.
- No productivity, inheritance, compounding, or safety claim. The claim is
  operator-controlled harness promotion, nothing more.

See INSPECTION.md for the RRSI source inspection and SUPPORTED.md for the
honest supported/unsupported statement.
