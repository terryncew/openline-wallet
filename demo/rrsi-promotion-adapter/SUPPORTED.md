# Supported / unsupported

A promotion adapter for RRSI-style harness candidates, demonstrated with a
synthetic candidate based on a byte-verified RRSI component.

## What this adapter establishes

- A proposed harness change can be exported as a signed package pinned to an
  exact base commit and an exact artifact hash.
- Acceptance first requires the candidate signer to match the optimizer
  identity the operator pinned at init. A different valid signing key is
  refused before the operator's checks run. The operator sets the pin when
  initializing the adapter home; the product makes the decision in code,
  not only in prose.
- The operator's checks run before anything is installed: base pinning,
  component allowlist, interface and dependency gate, and a smoke check in
  a subprocess. The subprocess receives no wallet/key paths or receiver
  credentials through its environment; it is not sandboxed and retains the
  host user's filesystem privileges.
- Installation is refused for a rejected candidate and for a candidate built
  on a changed base. Only the exact accepted artifact is installed.
- Invocation re-verifies the deployed bytes against the accepted hash, so a
  substituted artifact is refused at the next gated invocation.
- A non-operator caller is refused on the receiver-managed install path;
  only the operator identity may install through it.
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
- No OS-level isolation. Proof ceiling: operator-controlled promotion flow,
  not adversarial isolation. The receiver-managed install path grants no
  promotion authority to the optimizer; this single-host preview does not
  prevent same-user filesystem writes. A production deployment would need
  the optimizer and the protected runtime under different principals with
  the filesystem to match; that is outside this preview.
- No sandbox. The component runs as a normal subprocess with the host's
  privileges, minus the credentials the restricted environment withholds.
  Hashes identify the artifact; they do not certify it safe.
- No productivity, inheritance, compounding, or safety claim. The operator
  checks say whether this operator accepts this artifact, not whether the
  change is an improvement.
