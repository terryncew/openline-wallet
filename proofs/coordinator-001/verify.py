"""Offline consistency verifier; fixture keys are self-attested, not outside trust."""
import argparse
import hashlib
import json
from pathlib import Path

from coordinator import action, digest
from openline_wallet.crypto import verify_record
from openline_wallet.wallet import verify_bundle
from openline_wallet.clock import parse_time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_evidence(e):
    terms = e["agreement"]
    job = digest(terms)
    require(terms["buyer"]["principal"] != terms["worker"]["principal"], "distinct parties")
    require(terms["amount"] == 10000 and terms["currency"] == "SIM_USD", "simulated contract")
    state, agreed, previous = "OFFERED", set(), None
    candidate = evaluation = payment = None
    for event in e["events"]:
        require(event["job"] == job and event["previous"] == previous, "event chain")
        previous = digest(event)
        kind = event["kind"]
        if kind in ("agree", "submit", "accept"):
            p, presentation, receipt = event["payload"], event["presentation"], event["gate_receipt"]
            role = presentation["subject_id"]
            require(role in ("buyer", "worker"), "role")
            principal = terms[role]["principal"]
            bundle = event["bundle"]
            _, timeline = verify_bundle(bundle, now=parse_time(bundle["issued_at"], "bundle time"))
            decided = parse_time(receipt["decided_at"], "decision time")
            mandate = timeline.mandates.get(presentation["mandate_id"], {})
            require(mandate.get("status") == "ACTIVE", "active mandate")
            require(mandate.get("subject_id") == role and mandate.get("subject_public_key") == presentation["subject_public_key"], "mandate holder")
            require(action(kind, p) in mandate.get("scopes", []), "mandate scope")
            require(parse_time(mandate["expires_at"]) > decided and parse_time(bundle["expires_at"]) > decided, "unexpired authority")
            require(presentation["bundle_head_hash"] == timeline.head_hash, "authority head")
            require(receipt["mandate_id"] == presentation["mandate_id"] and receipt["subject_id"] == role, "receipt authority")
            require(int(decided.timestamp()) <= terms["deadline"], "contract deadline")
            require(bundle["principal"]["root_public_key"] == terms[role]["root"], "wallet root")
            require(verify_record(presentation, expected_public_key=presentation["subject_public_key"])[0], "holder signature")
            require(verify_record(receipt, expected_public_key=receipt["gate_public_key"])[0], "gate signature")
            require(receipt["decision"] == "ALLOWED" and receipt["reason_codes"] == [], "gate decision")
            require(receipt["presentation_hash"] == digest(presentation), "presentation binding")
            require(receipt["principal_id"] == presentation["principal_id"] == principal, "party binding")
            require(receipt["action"] == presentation["action"] == action(kind, p), "action binding")
            require(p["job"] == job, "agreement binding")
            if kind == "agree":
                require(state == "OFFERED" and role not in agreed and p == {"job": job}, "agreement transition")
                agreed.add(role)
                if len(agreed) == 2:
                    state = "AGREED"
            elif kind == "submit":
                require(state == "AGREED" and role == "worker", "submission transition")
                candidate, state = p["candidate"], "SUBMITTED"
            else:
                require(state == "ELIGIBLE" and role == "buyer", "acceptance transition")
                require(p == {"job": job, "candidate": candidate, "evaluation": evaluation}, "acceptance binding")
                state = "ACCEPTED"
        elif kind == "evaluation":
            v = event["evidence"]
            require(state == "SUBMITTED" and v["job"] == job and v["base"] == terms["base"] and v["candidate"] == candidate, "evaluation binding")
            require(v["checks"]["status"] == v["protected"]["status"] == "PASS", "evaluation pass")
            require(v["protected"]["touched"] == [], "protected paths")
            rows = v["checks"]["commands"]
            require([r["argv"] for r in rows] == terms["checks"], "exact checks")
            require(all(r["exit_code"] == 0 and not r["timed_out"] for r in rows), "checks passed")
            evaluation, state = digest(v), "ELIGIBLE"
        elif kind == "payment_intent":
            require(state in ("ACCEPTED", "PENDING"), "payment requires buyer acceptance")
            require(event["request"] == payment_request(terms, job), "payment intent binding")
            state = "PENDING"
        elif kind == "settlement":
            require(state == "PENDING", "settlement transition")
            payment = event["receipt"]
            require(verify_record(payment, expected_public_key=terms["provider_key"])[0], "provider signature")
            require(payment["request"] == payment_request(terms, job) and payment["status"] == "SETTLED", "provider binding")
            state = "SETTLED"
        else:
            raise ValueError("unknown event")
    require(state == e["final"]["status"] == "SETTLED", "terminal state")
    require(e["final"]["payment"] == payment and e["final"]["candidate"] == candidate and e["final"]["evaluation"] == evaluation, "terminal evidence")
    require(e["transfers"] == 1, "one transfer")
    require(e["balances"] == {terms["buyer"]["principal"]: 10000, terms["worker"]["principal"]: 10000}, "balances")
    require(sum(e["balances"].values()) == e["initial_total"] == 20000, "conservation")


def payment_request(terms, job):
    return {"id": job, "buyer": terms["buyer"]["principal"], "worker": terms["worker"]["principal"],
            "amount": terms["amount"], "currency": "SIM_USD"}


def verify(path, source):
    path, source = Path(path), Path(source)
    report = json.loads((path / "result.json").read_text())
    require(report["verdict"] == "CONTROLLED_SETTLEMENT_RECONCILIATION_PASSED", "verdict")
    require(report["tests_passed"] == len(report["tests"]) and report["tests_passed"] >= 18, "test count")
    require(set(report["files_sha256"]) == {"evidence.json", "tests.txt"}, "artifact inventory")
    require(set(report["source_sha256"]) == {"coordinator.py", "test_coordinator.py", "reproduce.py", "verify.py"}, "source inventory")
    for name, expected in report["files_sha256"].items():
        require(hashlib.sha256((path / name).read_bytes()).hexdigest() == expected, "artifact hash: " + name)
    for name, expected in report["source_sha256"].items():
        require(hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, "source hash: " + name)
    e = json.loads((path / "evidence.json").read_text())
    check_evidence(e)
    # Exercise semantic checks independently of the outer file hashes.
    mutations = [lambda x: x.update(transfers=2),
                 lambda x: x["agreement"].update(amount=1),
                 lambda x: x["final"]["payment"]["request"].update(worker="attacker"),
                 lambda x: x["events"].pop(0)]
    for mutate in mutations:
        bad = json.loads(json.dumps(e))
        mutate(bad)
        try:
            check_evidence(bad)
        except (ValueError, KeyError):
            continue
        raise ValueError("VERIFIER_MUTANT_SURVIVED")
    return {"valid": True, "semantic_mutations_rejected": len(mutations)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.path, Path(__file__).resolve().parent)))
