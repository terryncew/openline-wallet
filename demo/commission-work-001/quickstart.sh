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

say "HARDENING (fresh home): spent money is gone, not committable again"
export COMMISSION_HOME="${COMMISSION_HOME%-funds}-hardening"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 50 >/dev/null && $C offer --caller seller --price 50 >/dev/null
mkinput job-H-001 "hardening one" /tmp/cw-in-H1.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-H1.txt >/dev/null
JH1=$(job_by_nonce job-H-001)
$C work --caller seller --job "$JH1" >/dev/null
$C submit --caller seller --job "$JH1" >/dev/null
$C verify --caller owner --job "$JH1" >/dev/null
$C settle --caller owner --job "$JH1" >/dev/null
echo "(budget 50, spent 50: a second 50-unit commission is refused)"
mkinput job-H-002 "hardening two" /tmp/cw-in-H2.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-H2.txt 2>&1 || echo "(refused as expected: INSUFFICIENT_ALLOWANCE — spent plus reserved)"

say "CRASH-SETTLE (fresh home): the process dies after the transfer is written"
export COMMISSION_HOME="${COMMISSION_HOME%-hardening}-crashsettle"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 200 >/dev/null && $C offer --caller seller --price 50 >/dev/null
mkinput job-C-001 "crash settle" /tmp/cw-in-C1.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-C1.txt >/dev/null
JC=$(job_by_nonce job-C-001)
$C work --caller seller --job "$JC" >/dev/null
$C submit --caller seller --job "$JC" >/dev/null
$C verify --caller owner --job "$JC" >/dev/null
echo "(the process dies right after the transfer is written)"
COMMISSION_CRASH_AFTER=ledger $C settle --caller owner --job "$JC" 2>&1 || echo "(died as staged: CRASH_SIMULATED)"
echo "(reconcile reports the committed state truthfully — no fresh settlement recommended)"
$C reconcile | grep -B1 -A2 "$JC"
echo "(retry: completes the local records, appends no second transfer)"
$C settle --caller owner --job "$JC"
python3 -c "
import json, os
t = json.load(open(os.environ['COMMISSION_HOME'] + '/ledger.json'))['transfers']
print('transfers on record:', len(t), '— exactly one' if len(t) == 1 else '— WRONG')
"

say "PAYEE-SWAP (fresh home): the payee is rewritten in the mutable copy after verification"
export COMMISSION_HOME="${COMMISSION_HOME%-crashsettle}-payee"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 200 >/dev/null && $C offer --caller seller --price 50 >/dev/null
mkinput job-Y-001 "payee swap" /tmp/cw-in-Y1.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-Y1.txt >/dev/null
JY=$(job_by_nonce job-Y-001)
$C work --caller seller --job "$JY" >/dev/null
$C submit --caller seller --job "$JY" >/dev/null
$C verify --caller owner --job "$JY" >/dev/null
python3 -c "
import json, os
p = os.environ['COMMISSION_HOME'] + '/jobs.json'
jobs = json.load(open(p))
jobs['$JY']['agreement']['payee'] = 'stranger-principal'
json.dump(jobs, open(p, 'w'), indent=1, sort_keys=True)
print('(payee rewritten in jobs.json after verification)')
"
$C settle --caller owner --job "$JY" 2>&1 || echo "(refused as expected: AGREEMENT_NOT_AUTHENTIC — money moves only under the signed agreement)"

say "CONCURRENCY (fresh home): eight commissions race for one 200 allowance"
export COMMISSION_HOME="${COMMISSION_HOME%-payee}-race"
rm -rf "$COMMISSION_HOME"
$C init >/dev/null && $C delegate --caller owner --budget 200 >/dev/null && $C offer --caller seller --price 50 >/dev/null
for n in 1 2 3 4 5 6 7 8; do
  mkinput "job-Q-00$n" "race $n" /tmp/cw-in-Q$n.txt
  $C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-Q$n.txt >/tmp/cw-race-$n.log 2>&1 &
done
wait
wins=$(grep -l "agreement frozen" /tmp/cw-race-*.log | wc -l)
echo "won: $wins of 8 (expected exactly 4)"
$C status | grep -A2 "agent allowance"

say "INTERRUPTION (fresh home): the process dies mid-flow; reconciliation is read-only"
export COMMISSION_HOME="${COMMISSION_HOME%-race}-crash"
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

say "accounting review: the allowance is not the funding"
$C revoke --caller owner >/dev/null
$C delegate --caller owner --budget 20000 >/dev/null
$C offer --caller seller --price 15000 >/dev/null
mkinput job-F-001 "funded" /tmp/cw-in-F.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-F.txt 2>&1 || echo "(refused as expected: INSUFFICIENT_OWNER_FUNDS — the funded balance cannot cover a 15,000 commitment)"
$C offer --caller seller --price 50 >/dev/null

say "monetary inputs are validated before signing: negative and zero prices refused"
$C offer --caller seller --price -50 2>&1 || echo "(refused as expected: INVALID_AMOUNT)"
$C offer --caller seller --price 0 2>&1 || echo "(refused as expected: INVALID_AMOUNT — zero-price work is not supported)"

say "re-granting preserves live commitments; a grant cannot shrink under them"
mkinput job-G-001 "grant" /tmp/cw-in-G.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/cw-in-G.txt >/dev/null
JG=$(job_by_nonce job-G-001)
$C revoke --caller owner >/dev/null
$C delegate --caller owner --budget 300 | grep -o "reserved 50 SIM_USD (simulated)" && echo "(reserved carried over the re-grant)"
$C revoke --caller owner >/dev/null
$C delegate --caller owner --budget 40 2>&1 || echo "(refused as expected: ALLOWANCE_REDUCTION_REFUSED)"
$C delegate --caller owner --budget 300 >/dev/null

say "verification is idempotent; a rejection releases exactly once"
$C work --caller seller --job "$JG" --wrong-input >/dev/null
$C submit --caller seller --job "$JG" >/dev/null
$C verify --caller owner --job "$JG" >/dev/null || true
$C verify --caller owner --job "$JG" 2>&1 | head -1 || true
$C status | grep -A1 "agent allowance"


echo
echo "quickstart complete."
