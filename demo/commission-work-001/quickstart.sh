#!/bin/bash
# commission-work quickstart: the full experience, locally, ~seconds, $0.
# Copyable. Every refusal below is expected; the script captures them.
set -u
export COMMISSION_HOME="${1:-$HOME/.commission-quickstart}"
rm -rf "$COMMISSION_HOME"

C="python3 $(dirname "$0")/commission.py"

say() { echo; echo "=== $1 ==="; }

mkinput() { printf '{"nonce": "%s"}\n%s\n' "$1" "$2" > "$3"; }

job_by_nonce() {
  python3 -c "
import json, os
d = json.load(open(os.environ['COMMISSION_HOME'] + '/jobs.json'))
print([k for k, v in d.items() if v['agreement']['nonce'] == '$1'][0])"
}

say "owner sets up; delegates a bounded budget to its agent; seller offers the service"
$C init | head -6
$C delegate --caller owner --budget 200
$C offer --caller seller --price 50 | head -4

say "JOB A: agreement freezes, seller works, receiver accepts, one settlement"
mkinput job-A-001 "the quick brown fox jumps over the lazy dog" /tmp/cw-in-A.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-A.txt | head -4
JA=$(job_by_nonce job-A-001)
$C work --caller seller --job "$JA" | head -2
$C submit --caller seller --job "$JA" | head -2
$C verify --caller owner --job "$JA" | head -3
$C settle --caller owner --job "$JA"
$C receipt --job "$JA" >/dev/null && echo "(signed settlement receipt verifies)"
say "replay settle: refused, no second payment"
$C settle --caller owner --job "$JA" 2>&1 || echo "(refused as expected: ALREADY_SETTLED)"

say "JOB B: bad work is rejected by the receiver; no payment; reservation released"
mkinput job-B-001 "hello world" /tmp/cw-in-B.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-B.txt >/dev/null
JB=$(job_by_nonce job-B-001)
$C work --caller seller --job "$JB" --wrong-input >/dev/null
$C submit --caller seller --job "$JB" >/dev/null
$C verify --caller owner --job "$JB" | head -2 || true
$C settle --caller owner --job "$JB" 2>&1 || echo "(refused as expected: SETTLEMENT_REFUSED)"

say "agent permissions are narrower than owner permissions"
mkinput job-X-001 "x" /tmp/cw-in-X.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-X.txt --raise-budget 1000 2>&1 || echo "(refused as expected: BUDGET_INCREASE_REFUSED)"
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-X.txt --rewrite-acceptance 2>&1 || echo "(refused as expected: ACCEPTANCE_REWRITE_REFUSED)"
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-X.txt --payee attacker 2>&1 || echo "(refused as expected: PAYEE_CHANGE_REFUSED)"
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-X.txt --self-authorize 2>&1 || echo "(refused as expected: SELF_AUTHORIZATION_REFUSED)"

say "altered agreement: verify refuses"
mkinput job-T-001 "tamper me" /tmp/cw-in-T.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-T.txt >/dev/null
JT=$(job_by_nonce job-T-001)
$C work --caller seller --job "$JT" >/dev/null
$C submit --caller seller --job "$JT" >/dev/null
$C verify --caller owner --job "$JT" --tamper-agreement amount 2>&1 | grep -E "FAIL|REJECTED" | head -3 || true

say "revocation blocks new work; the already-earned obligation still settles"
mkinput job-R-001 "revocation test" /tmp/cw-in-R.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-R.txt >/dev/null
JR=$(job_by_nonce job-R-001)
$C work --caller seller --job "$JR" >/dev/null
$C submit --caller seller --job "$JR" >/dev/null
$C verify --caller owner --job "$JR" >/dev/null
$C revoke --caller owner
mkinput job-R-002 "after revoke" /tmp/cw-in-R2.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-R2.txt 2>&1 || echo "(refused as expected: MANDATE_REVOKED)"
$C settle --caller owner --job "$JR"

say "final state of the main home"
$C status

say "FUNDS (fresh home): reserved before work; parallel jobs cannot commit the same balance"
export COMMISSION_HOME="${COMMISSION_HOME}-funds"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 200 >/dev/null && $C offer --caller seller --price 50 >/dev/null
for n in 1 2 3 4; do
  mkinput "job-P-00$n" "parallel $n" /tmp/cw-in-P$n.txt
  $C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-P$n.txt >/dev/null
done
$C status | grep -A2 "agent allowance"
mkinput job-P-005 "parallel 5" /tmp/cw-in-P5.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-P5.txt 2>&1 || echo "(refused as expected: ALREADY_RESERVED)"
$C offer --caller seller --price 999 >/dev/null
mkinput job-P-006 "too rich" /tmp/cw-in-P6.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-P6.txt 2>&1 || echo "(refused as expected: INSUFFICIENT_ALLOWANCE or ALREADY_RESERVED)"
$C reconcile

say "INTERRUPTION (fresh home): the process dies mid-flow; reconciliation is read-only"
export COMMISSION_HOME="${COMMISSION_HOME%-funds}-crash"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 200 >/dev/null && $C offer --caller seller --price 50 >/dev/null
mkinput job-I-001 "interruption one" /tmp/cw-in-I1.txt
mkinput job-I-002 "interruption two" /tmp/cw-in-I2.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-I1.txt >/dev/null
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-I2.txt >/dev/null
JI1=$(job_by_nonce job-I-001)
JI2=$(job_by_nonce job-I-002)
$C work --caller seller --job "$JI1" >/dev/null   # crash before submit
$C work --caller seller --job "$JI2" >/dev/null
$C submit --caller seller --job "$JI2" >/dev/null
$C verify --caller owner --job "$JI2" >/dev/null  # crash before settle
echo "(process dies here)"
$C reconcile
echo "(restart: re-running work is idempotent; settle happens exactly once)"
$C work --caller seller --job "$JI1"
$C submit --caller seller --job "$JI1" >/dev/null
$C verify --caller owner --job "$JI1" >/dev/null
$C settle --caller owner --job "$JI1" >/dev/null
$C settle --caller owner --job "$JI2" >/dev/null
$C settle --caller owner --job "$JI2" 2>&1 || echo "(refused as expected: ALREADY_SETTLED)"

echo
echo "quickstart complete."
