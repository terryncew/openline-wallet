"""Reproduce the pinned legacy failure and the local effect-closure repair.

Run from a source checkout with:
python proofs/wallet-effect-closure-001/reproduce.py --output /tmp/wallet-effect-closure
All keys are disposable; private material is never included in the output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import pretty_json, strict_json_load
from openline_wallet.crypto import public_key_hex, record_hash, sign_record, verify_record
from openline_wallet.effect_closure import EffectClosure, EffectGate
from openline_wallet.errors import WalletError
from openline_wallet.receiver import create_presentation
from openline_wallet.storage import atomic_write_json
from openline_wallet.wallet import Wallet
import openline_wallet.effect_closure as effect_source
import openline_wallet.gate_http as http_source

BASE = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
ACTION = "deploy:staging"
BASE_COMMIT = "9df63798c0f520e9d6c8dc85dd293ebdeaa9c11f"
SOURCE_BLOBS = {
    "wallet.py": "bfced3324554cb7f3b12b1b1aa373a57bc63881b",
    "receiver.py": "0844f8e08c12e036f601810a3f9a30c56076ca7e",
    "gate_http.py": "192006de121e9ee02f7384861e71a0011607735f",
}


def require(condition):
    if not condition:
        raise RuntimeError("EFFECT_CLOSURE_PROOF_FAILED")


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def load_baseline():
    source = Path(__file__).with_name("baseline_gate_http.py")
    data = source.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != SOURCE_BLOBS["gate_http.py"]:
        raise RuntimeError("pinned baseline source hash mismatch")
    name = "openline_wallet._effect_closure_baseline"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture(path):
    wallet = Wallet.create(path/"wallet", now=BASE)
    key = Ed25519PrivateKey.generate()
    wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(key),
                 scopes=[ACTION], expires_at=at(3600), now=BASE, mandate_id="grant-a")
    return wallet, key, wallet.export_bundle(now=at(1))


def presentation(gate, wallet, key, bundle):
    challenge = gate.issue_challenge(principal_id=wallet.principal_id,
        subject_id="agent-a", action=ACTION, now=at(2))
    return create_presentation(bundle=bundle, mandate_id="grant-a", subject_id="agent-a",
        subject_key=key, action=ACTION, receiver_challenge=challenge, now=at(2))


def revocation(wallet):
    wallet.revoke("grant-a", now=at(3))
    return wallet.export_bundle(now=at(4))


def baseline_experiment(path):
    module = load_baseline()
    wallet, key, bundle = fixture(path)
    runtime = module.ReceiverRuntime.create(gate_id="baseline-receiver",
        principal_id=wallet.principal_id, root_public_key=wallet.root_public_key,
        ledger_path=path/"effects.json", receipts_dir=path/"receipts")
    runtime.gate.admit_bundle(bundle, now=at(1))
    body = {"presentation": presentation(runtime.gate, wallet, key, bundle),
            "action": ACTION, "release": "baseline-held"}
    entered, release = threading.Event(), threading.Event()
    result = {}
    errors = []
    def held_write(target, value, **kwargs):
        if Path(target) == runtime.ledger_path:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("baseline effect hold timed out")
        return atomic_write_json(target, value, **kwargs)
    def execute():
        try:
            result.update(runtime.execute(body))
        except Exception as exc:
            errors.append(exc)
    with (
        patch("openline_wallet.receiver.utc_now", return_value=at(2)),
        patch.object(module, "atomic_write_json", side_effect=held_write),
    ):
        worker = threading.Thread(target=execute)
        worker.start()
        if not entered.wait(5):
            release.set()
            worker.join(5)
            raise RuntimeError("baseline did not reach the held effect writer")
        current = revocation(wallet)
        admitted = runtime.gate.admit_bundle(current, now=at(4))
        release.set()
        worker.join(5)
    if worker.is_alive() or errors:
        raise RuntimeError(f"baseline worker failed: {errors}")
    effects = strict_json_load(runtime.ledger_path)
    require(result["decision"] == "ALLOWED" and result["effect_applied"])
    require(len(effects) == 1 and effects[0]["release"] == "baseline-held")
    require(runtime.gate._admitted[wallet.principal_id].timeline.mandates["grant-a"]["status"] == "REVOKED")
    return {"admission": result["receipt"], "effects": effects, "new_head": admitted,
            "observed": {"effects_after_newer_revocation_admission": len(effects),
                         "closure_certificate": False,
                         "interpretation": "Upstream admission is insufficient; the old API did not claim effect closure."}}


def patched_experiment(path):
    wallet, key, bundle = fixture(path)
    gate = EffectGate("effect-closure-receiver")
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle, now=at(1))
    effects = EffectClosure(gate, path/"effects.json", path/"receipts")
    try:
        prepared = effects.prepare(presentation(gate, wallet, key, bundle),
                                   action=ACTION, release="patched-held", now=at(2))
        require(prepared["decision"] == "PREPARED")
        closure = effects.close(revocation(wallet), "grant-a", now=at(4))
        result = effects.finish(prepared["ticket"], action=ACTION,
                                release="patched-held", now=at(5))
        ledger = strict_json_load(path/"effects.json") if (path/"effects.json").exists() else []
        require(closure["status"] == "EFFECT_CLOSED" and closure["pending_fenced"] == 1)
        require(result["decision"] == "STOPPED" and result["reason_codes"] == ["MANDATE_REVOKED"])
        require(not result["effect_applied"] and ledger == [])
        require(verify_record(closure, expected_public_key=gate.public_key)[0])
        require(verify_record(result["receipt"], expected_public_key=gate.public_key)[0])
        require(verify_record(result["effect_receipt"], expected_public_key=gate.public_key)[0])
        wallet.add_receipt(result["receipt"])
        return {"admission": result["admission_receipt"], "frontier": result["receipt"],
                "effect_receipt": result["effect_receipt"], "closure": closure, "effects": ledger,
                "observed": {"effects_after_signed_closure": len(ledger),
                             "pending_fenced": closure["pending_fenced"],
                             "final_reason": result["reason_codes"],
                             "wallet_receipts": len(wallet.state["receipts"])}}
    finally:
        effects.shutdown()


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("evidence output must be empty")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        baseline = baseline_experiment(root/"baseline")
        patched = patched_experiment(root/"patched")
    records = {
        "baseline-admission.json": baseline["admission"],
        "baseline-effects.json": baseline["effects"],
        "closure.json": patched["closure"],
        "admission.json": patched["admission"],
        "frontier.json": patched["frontier"],
        "effect-receipt.json": patched["effect_receipt"],
        "patched-effects.json": patched["effects"],
    }
    for name, record in records.items():
        (output/name).write_text(pretty_json(record), encoding="ascii")
    source_hashes = {
        "src/openline_wallet/effect_closure.py": hashlib.sha256(Path(effect_source.__file__).read_bytes()).hexdigest(),
        "src/openline_wallet/gate_http.py": hashlib.sha256(Path(http_source.__file__).read_bytes()).hexdigest(),
    }
    receipt = sign_record({
        "schema": "openline.wallet.effect_closure_experiment.v1",
        "experiment_id": "WALLET-EFFECT-CLOSURE-001",
        "base_commit": BASE_COMMIT,
        "source_git_blobs": SOURCE_BLOBS,
        "candidate_source_sha256": source_hashes,
        "baseline": baseline["observed"],
        "patched": patched["observed"],
        "checks": {"baseline_late_effect_reproduced": True,
                   "revocation_closure_signed": True,
                   "held_action_fenced": True,
                   "zero_post_closure_effects": True,
                   "original_admission_preserved": True,
                   "wallet_receipt_schema_preserved": True},
        "verdict": "LOCAL_EFFECT_CLOSURE_ENFORCED",
        "claim_boundary": "One receiver-owned process and its local staging ledger. No external provider queue, multi-receiver closure, or production recovery claim.",
        "verification": "Self-attested disposable test key; not an independent witness.",
        "ci_status": "PENDING_REMOTE_RUN",
    }, Ed25519PrivateKey.generate())
    require(verify_record(receipt)[0])
    (output/"result.json").write_text(pretty_json(receipt), encoding="ascii")
    lines = []
    for file in sorted(output.iterdir()):
        if file.is_file():
            lines.append(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}")
    (output/"SHA256SUMS.txt").write_text("\n".join(lines)+"\n", encoding="ascii")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(pretty_json({"verdict": result["verdict"], "result_hash": record_hash(result),
                       "source_sha256": result["candidate_source_sha256"]}))


if __name__ == "__main__":
    main()
