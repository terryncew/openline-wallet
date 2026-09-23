# RRSI-to-OpenLine promotion adapter (developer preview)

An optimizer may propose a harness change. Only the operator-owned receiver
may install that exact change into the protected runtime.

This is a thin adapter, not a new system. It reuses the merged
capability-installer machinery (package validation, wallet mandates,
revocation) and adds the promotion-specific pieces: git-candidate export with
exact base and artifact hashes, operator-owned protected checks, a guarded
deployment dir, and clean-environment invocation.

## Run it

```bash
cd demo/rrsi-promotion-adapter
bash quickstart.sh            # full demo, ~3s, zero paid calls
bash quickstart.sh /tmp/demo  # work dir of your choice
```

The quickstart builds a synthetic candidate repo, exports a candidate, runs
the operator checks, installs, invokes on fresh local work, then demonstrates
four blocked bypasses (changed base, rejected candidate, direct optimizer
write, substituted artifact) and revocation.

## What it demonstrates

1. Export a candidate with exact base hash and resulting artifact hash.
2. Evaluate it against operator-owned protected checks.
3. Refuse installation on rejection or a changed base.
4. Install only the exact accepted artifact.
5. Invoke it on fresh local work and record the loaded digest.
6. Refuse substitution and direct optimizer writes.
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
- The optimizer is a separate local key. The optimizer has no write authority
  over the protected deployment dir; only the operator identity may install.
  On this single host that boundary is enforced by the adapter's guarded
  write path, not by OS user separation. See SUPPORTED.md.
- The component runs in a subprocess with a clean environment: no wallet, no
  keys, no receiver credentials. Hashes identify the artifact; they do not
  certify it safe.
- No productivity, inheritance, compounding, or safety claim. The claim is
  operator-controlled harness promotion, nothing more.

See INSPECTION.md for the RRSI source inspection and SUPPORTED.md for the
honest supported/unsupported statement.
