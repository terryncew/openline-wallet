# Supported / unsupported

## What this adapter establishes

- A proposed harness change can be exported as a signed package pinned to an
  exact base commit and an exact artifact hash.
- The operator's checks run before anything is installed: base pinning,
  component allowlist, interface and dependency gate, and a smoke check in a
  subprocess with a clean environment (no wallet, no keys, no receiver
  credentials).
- Installation is refused for a rejected candidate and for a candidate built
  on a changed base. Only the exact accepted artifact is installed.
- Invocation re-verifies the deployed bytes against the accepted hash, so a
  substituted artifact is refused at the next gated invocation.
- A direct write attempt by the optimizer identity into the protected
  deployment dir is refused; only the operator identity may install.
- Revocation ends future invocation through the receiver; the next gated
  invoke is refused with the mandate gone.
- The claim is operator-controlled harness promotion. Nothing more.

## What it does not establish

- No whole-harness promotion. One small, deterministic, stdlib-only
  component (`terminus_json_plain_parser.py`). The single-component machinery
  is not a harness deployment system.
- No RRSI-generated improvement. The candidates are synthetic integration
  fixtures, labeled as such. The public RRSI repo contains no preserved
  candidate commits to promote.
- No OS-level isolation. On this single host the optimizer/operator boundary
  is enforced by the adapter's guarded write path and the receiver's mandate
  checks, not by separate OS users, containers, or hosts. A production
  deployment would need the optimizer and the protected runtime under
  different principals with the filesystem to match; that is outside this
  preview.
- No sandbox. The component runs as a normal subprocess with the host's
  privileges, minus the credentials the clean environment withholds. Hashes
  identify the artifact; they do not certify it safe.
- No productivity, inheritance, compounding, or safety claim. The operator
  checks say whether this operator accepts this artifact, not whether the
  change is an improvement.
- No seller trust. The optimizer's signature identifies the packaging key;
  this preview provides no optimizer trust registry. The operator decides
  whether that signer is trusted.
