"""Read-only reappraisal with explicit receiver, principal, and target binding.

Historical expectations are pinned independently of the evidence being checked.
Fresh fixture packets require an explicit self-attested mode or caller-supplied
trusted context. Neither mode grants execution authority or verifies a live
provider outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from typing import Any, Mapping

from openline_wallet.canonical import strict_json_load, canonical_json
from openline_wallet.clock import parse_time
from openline_wallet.crypto import record_hash, verify_record, principal_id
from openline_wallet.wallet import verify_bundle
from openline_wallet.github_effect import MergeTarget, _snapshot as provider_snapshot

ROOT = Path(__file__).resolve().parents[2]
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DECISION_SCHEMA = "openline.gate.action_receipt.v1"
EFFECT_SCHEMA = "openline.wallet.github_merge_effect.v1"
CLOSURE_SCHEMA = "openline.wallet.github_merge_closure.v1"
RESULT_SCHEMA = "openline.wallet.provider_effect_experiment.v1"

FROZEN_RESULT_SHA256 = '85580e7da1aa01205635cee321d2c608a8c87bc07016dc64f9d67ac04f33bd98'
HISTORICAL_SOURCE_SHA256 = {'proofs/provider-effect-001/reproduce.py': 'bf07f3f97b1bdb44b111a08bf56be418aa865e2b51d8a02012acddf1ac47ceed', 'proofs/provider-effect-001/verify.py': 'fa31ab4511353dda0cf59e81fd915c300235393b476df6551c77fda5798f3abc', 'src/openline_wallet/github_effect.py': 'b4e50a1ff1b047f6538e10e2cc0d4a95063a6872a3d0bbdcc1a039715a9413e6', 'src/openline_wallet/github_effect_live.py': 'a3b4dc8582ab4e5edc2f6430325d26da17c67faf877df57e8ecf3e4396f7d977', 'tests/github_http_fixture.py': '5d8447059f6f0d22b6a52789ec525d0d0dfdb36af1a013ad5e4a7e8374443e47', 'tests/test_github_effect.py': 'ea38a86b0c3cf7a32b5998571b2e03886f285547ca71053ebd754fcfc0e5ba55', 'tests/test_github_http.py': '9d5b1c9b4e83c88aad131780bfc3aa586407c16b0cf253961b5ae87ccb011c41'}
HISTORICAL_SOURCE_ARCHIVE_SHA256 = '509ad83e928adcd0f39ca96e8a1344d3dd278b221afc7c885503f9879b9649fc'
FROZEN_CONTEXT = {'principal': {'principal_id': 'openline:principal:0de930c27444f6c2ea9be3f746110fbbe66bc10a63c7d69a2a370f2dbb29457b', 'root_public_key': '07df877680d3bb194efaaa73175054753b375bf0ee65e0c9753d1cb8bde2ffd8'}, 'target': {'action': 'github:merge:e526e6ccfece32522601a6fc373a5bf23132e87dc8a66e2f3c9fb990b3d96a1b', 'base_ref': 'olp-test-base', 'base_sha': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'head_sha': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'number': 7, 'repository': 'example/provider-effect-sandbox', 'repository_id': 12345, 'schema': 'openline.wallet.github_merge_action.v1'}, 'receivers': {'a': {'gate_id': 'provider-effect-a', 'gate_public_key': '5fa440eae45924ef987bf33dcf8f6f4b91c06b56ce37b571ccc9a50b260ff982', 'mandate_id': 'grant-a', 'subject_id': 'agent-a', 'subject_public_key': '4ad6ecd97d8300e9aa40ae074853b58c90903b347978d0025f4d30523c740e70'}, 'b': {'gate_id': 'provider-effect-b', 'gate_public_key': 'b03adea3ead560685553e1de34492a0d156e1667b240a713f7d864e1d540dad6', 'mandate_id': 'grant-b', 'subject_id': 'agent-b', 'subject_public_key': 'a1099713eb6784f88bbc3bbcd791192229ce70a8869e10cc36702c4eddc58de9'}}, 'unsafe': {'principal': {'principal_id': 'openline:principal:bbc27e583e8bb436d7011b0ebc5db5593da329b92dfe862b49ccc8424e194911', 'root_public_key': '93ee05e63facc0613264203eff4b8bb56c1be026f1333873f6beb1f4d2f8daa4'}, 'gate_id': 'unsafe-admission-only', 'gate_public_key': '1628803f894069ba6f12a2e4448baa1be7c76813a24eefb641cf566230a77222', 'subject_public_key': 'da64b1f5831406a8a260bd7fba34593066dec42b9aa1b95fb7329110a0d28457'}}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verified(record: Mapping[str, Any], key: str | None = None) -> Mapping[str, Any]:
    require(verify_record(record, expected_public_key=key)[0], "signed evidence invalid")
    return record


def equal(value: Any, expected: Any, label: str) -> None:
    require(value == expected, label + " mismatch")


def _read(path: Path) -> Any:
    return strict_json_load(path)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(path: Path, names: set[str]) -> None:
    rows = (path / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    sums = {}
    for row in rows:
        digest, separator, name = row.partition("  ")
        require(separator and HEX64.fullmatch(digest) is not None and
                name not in sums and name in names, "manifest invalid")
        sums[name] = digest
    equal(set(sums), names, "manifest file set")
    for name, digest in sums.items():
        equal(_hash((path / name).read_bytes()), digest, "manifest hash")


def _source_closure(result: Mapping[str, Any], *, historical: bool) -> str:
    sources = result["source_sha256"]
    require(isinstance(sources, dict) and set(sources) == set(HISTORICAL_SOURCE_SHA256), "source set invalid")
    if historical:
        equal(sources, HISTORICAL_SOURCE_SHA256, "historical source set")
        archive = ROOT / "proofs/provider-effect-001/historical-source-packet.zip"
        equal(_hash(archive.read_bytes()), HISTORICAL_SOURCE_ARCHIVE_SHA256,
              "historical source archive")
        with zipfile.ZipFile(archive) as source:
            require(source.testzip() is None, "historical source archive invalid")
            equal(set(source.namelist()), set(sources), "historical source files")
            for name, digest in sources.items():
                equal(_hash(source.read(name)), digest, "historical source hash")
        return "RECORDED_HISTORICAL_SOURCE"
    for name, digest in sources.items():
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts and
                path.parts and path.suffix == ".py", "unsafe source path")
        equal(_hash((ROOT / path).read_bytes()), digest, "source changed: " + name)
    return "CURRENT_SOURCE_HASHES"


def _packet(path: Path, *, top: bool) -> Mapping[str, Any]:
    result = verified(_read(path / "result.json"))
    equal(result["schema"], RESULT_SCHEMA, "result schema")
    equal(result["experiment_id"], "PROVIDER-EFFECT-001", "experiment")
    equal(result["verdict"], "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED", "verdict")
    require(result["transport"] in {"fixture", "LOOPBACK_HTTP_FIXTURE"},
            "transport invalid")
    if top:
        equal(result["transport"], "LOOPBACK_HTTP_FIXTURE", "top transport")
        require(result["live_github_run"] is False, "live claim invalid")
        require(all(v is True for v in result["checks"].values()) and
                len(result["checks"]) == 7, "frozen checks invalid")
    expected = result["evidence_sha256"]
    require(isinstance(expected, dict) and expected, "evidence set invalid")
    actual = {str(p.relative_to(path)) for p in path.rglob("*.json")
              if p != path / "result.json"}
    equal(actual, set(expected), "evidence file set")
    for name, digest in expected.items():
        part = Path(name)
        require(not part.is_absolute() and ".." not in part.parts and
                part.suffix == ".json", "unsafe evidence path")
        equal(_hash((path / part).read_bytes()), digest, "evidence hash")
    _manifest(path, actual | {"result.json"})
    return result


def _bundle(bundle: Mapping[str, Any], root_key: str, expected_principal: str):
    verified_bundle, timeline = verify_bundle(
        bundle, now=parse_time(bundle["issued_at"]), require_fresh=False)
    equal(verified_bundle["principal"]["root_public_key"], root_key, "principal root")
    equal(verified_bundle["principal"]["principal_id"], expected_principal, "principal")
    equal(principal_id(root_key), expected_principal, "principal derivation")
    return verified_bundle, timeline


def _target(record: Mapping[str, Any], expected: Mapping[str, Any]) -> MergeTarget:
    equal(record, expected, "target")
    target = MergeTarget(**{k: record[k] for k in
        ("repository", "repository_id", "number", "head_sha", "base_ref", "base_sha")})
    equal(target.to_record(), expected, "canonical target")
    return target


def _context_record(record: Mapping[str, Any], context: Mapping[str, Any],
                    phase: str, schema: str, *, action: str | None = None,
                    subject: bool = False) -> Mapping[str, Any]:
    receiver = context["receivers"][phase]
    verified(record, receiver["gate_public_key"])
    expected = {
        "schema": schema,
        "decision_authority": "RECEIVER_GATE",
        "wallet_policy_authority": "NONE",
        "principal_id": context["principal"]["principal_id"],
        "gate_id": receiver["gate_id"],
        "gate_public_key": receiver["gate_public_key"],
        "mandate_id": receiver["mandate_id"],
    }
    if action is not None:
        expected["action"] = action
    if subject:
        expected["subject_id"] = receiver["subject_id"]
    for name, value in expected.items():
        equal(record[name], value, name)
    return record


def _decision(record, context, phase, action, decision, reasons):
    _context_record(record, context, phase, DECISION_SCHEMA,
                    action=action, subject=True)
    equal(record["decision"], decision, "decision")
    equal(record["reason_codes"], reasons, "reason codes")
    return record


def _timeline(bundle, timeline, context, *, phase: str | None = None):
    events = bundle["events"]
    if phase is None:
        expected = [
            ("MANDATE_ISSUED", "grant-a"),
            ("MANDATE_REVOKED", "grant-a"),
            ("MANDATE_ISSUED", "grant-b"),
            ("MANDATE_REVOKED", "grant-b"),
        ]
    else:
        expected = [("MANDATE_ISSUED", "grant-a"), ("MANDATE_REVOKED", "grant-a")]
    equal(len(events), len(expected), "timeline length")
    for i, (event, (kind, mandate)) in enumerate(zip(events, expected), 1):
        equal(event["sequence"], i, "event sequence")
        equal(event["event_type"], kind, "event type")
        equal(event["data"]["mandate_id"], mandate, "event mandate")
        equal(event["principal_id"], context["principal"]["principal_id"], "event principal")
        if kind == "MANDATE_REVOKED":
            equal(event["data"]["reason"], "USER_REVOKED", "revocation reason")
        else:
            receiver = context["receivers"][mandate[-1]]
            equal(event["data"]["subject_id"], receiver["subject_id"], "grant subject")
            equal(event["data"]["subject_public_key"], receiver["subject_public_key"], "grant key")
            require(context["target"]["action"] in event["data"]["scopes"], "grant scope invalid")
        equal(timeline.mandates[mandate]["status"], "REVOKED", "mandate standing")
    equal(bundle["head"]["sequence"], len(events), "head sequence")
    equal(bundle["head"]["event_hash"], record_hash(events[-1]), "head hash")


def _snapshot(record, target, *, merged, state, initial=False):
    for name in ("repository", "repository_id", "number", "head_sha", "base_ref"):
        equal(record[name], getattr(target, name), "snapshot " + name)
    equal(record["merged"], merged, "snapshot merged")
    equal(record["state"], state, "snapshot state")
    if initial:
        equal(record["base_sha"], target.base_sha, "initial base")
        require(record["merge_commit_sha"] is None, "initial merge SHA invalid")
    else:
        require(HEX40.fullmatch(record["base_sha"]) is not None, "base SHA invalid")


def _historical_context() -> dict[str, Any]:
    return json.loads(json.dumps(FROZEN_CONTEXT))


def fixture_context(output: Path) -> dict[str, Any]:
    """Explicitly self-attested fixture bootstrap; never a trust anchor."""
    repaired = Path(output) / "repaired"
    bundle = _read(repaired / "revoked_b.json")
    context = {"principal": bundle["principal"], "target": _read(repaired / "target.json"),
               "receivers": {}, "unsafe": {}}
    for phase in ("a", "b"):
        admission = _read(repaired / ("admission_" + phase + ".json"))
        grant = next(e["data"] for e in bundle["events"]
                     if e["event_type"] == "MANDATE_ISSUED" and
                        e["data"]["mandate_id"] == "grant-" + phase)
        context["receivers"][phase] = {
            "gate_id": "provider-effect-" + phase,
            "gate_public_key": admission["gate_public_key"],
            "mandate_id": "grant-" + phase,
            "subject_id": "agent-" + phase,
            "subject_public_key": grant["subject_public_key"]}
    unsafe = _read(Path(output) / "unsafe-control.json")
    context["unsafe"] = {
        "principal": unsafe["revoked_bundle"]["principal"],
        "gate_id": "unsafe-admission-only",
        "gate_public_key": unsafe["admission"]["gate_public_key"],
        "subject_public_key": next(e["data"]["subject_public_key"]
            for e in unsafe["revoked_bundle"]["events"]
            if e["event_type"] == "MANDATE_ISSUED")}
    return context


def _check_unsafe(unsafe, context, target):
    info = context["unsafe"]
    bundle, timeline = _bundle(unsafe["revoked_bundle"],
        info["principal"]["root_public_key"], info["principal"]["principal_id"])
    admission = unsafe["admission"]
    verified(admission, info["gate_public_key"])
    for name, value in {
        "schema": DECISION_SCHEMA, "gate_id": info["gate_id"],
        "gate_public_key": info["gate_public_key"],
        "principal_id": info["principal"]["principal_id"],
        "mandate_id": "grant-a", "subject_id": "agent-a",
        "action": target.action, "decision": "ALLOWED",
        "decision_authority": "RECEIVER_GATE", "wallet_policy_authority": "NONE",
        "reason_codes": [],
    }.items():
        equal(admission[name], value, "unsafe " + name)
    events = bundle["events"]
    equal(len(events), 2, "unsafe event count")
    equal([e["event_type"] for e in events], ["MANDATE_ISSUED","MANDATE_REVOKED"], "unsafe events")
    equal(events[0]["data"]["subject_public_key"], info["subject_public_key"], "unsafe subject key")
    equal(events[0]["data"]["scopes"], [target.action], "unsafe scope")
    equal(timeline.mandates["grant-a"]["status"], "REVOKED", "unsafe standing")
    equal(unsafe["claimed"]["claim"], "ADMISSION_ONLY_EFFECTIVE", "unsafe claim")
    equal(unsafe["claimed"]["head_hash"], bundle["head"]["event_hash"], "unsafe head")
    equal(unsafe["merge_requests"], 1, "unsafe merge count")
    require(unsafe["late_effect_reproduced"] is True, "unsafe control invalid")
    response = unsafe["response"]
    equal(response["merged"], True, "unsafe response")
    require(HEX40.fullmatch(response["sha"]) is not None, "unsafe SHA invalid")
    _snapshot(provider_snapshot(unsafe["after"], target), target, merged=True, state="closed")
    equal(unsafe["after"]["merge_commit_sha"], response["sha"], "unsafe merge SHA")


def _check_repaired(path, context):
    target = _target(_read(path / "target.json"), context["target"])
    action = target.action
    bundle = _read(path / "revoked_b.json")
    verified_bundle, timeline = _bundle(bundle, context["principal"]["root_public_key"],
                                        context["principal"]["principal_id"])
    _timeline(bundle, timeline, context)
    events = bundle["events"]
    a, b = (_read(path / ("admission_" + p + ".json")) for p in ("a","b"))
    closure_a, closure_b = (_read(path / ("closure_" + p + ".json")) for p in ("a","b"))
    stopped, effect = (_read(path / (p + ".json")) for p in ("stopped_a","effect_b"))
    _decision(a, context, "a", action, "ALLOWED", [])
    _decision(b, context, "b", action, "ALLOWED", [])
    equal(stopped["admission_receipt"], a, "stopped admission")
    equal(effect["admission_receipt"], b, "effect admission")
    stop = _decision(stopped["receipt"], context, "a", action,
                     "STOPPED", ["MANDATE_REVOKED"])
    frontier = _decision(effect["receipt"], context, "b", action, "ALLOWED", [])
    equal(stopped["decision"], "STOPPED", "stopped decision")
    equal(stopped["reason_codes"], ["MANDATE_REVOKED"], "stopped reason")
    require(stopped["effect_applied"] is False, "stopped effect invalid")
    equal(effect["decision"], "ALLOWED", "effect decision")
    equal(effect["reason_codes"], [], "effect reasons")
    require(effect["effect_applied"] is True, "effect not applied")
    equal(a["presentation_hash"], stop["presentation_hash"], "A presentation")
    equal(b["presentation_hash"], frontier["presentation_hash"], "B presentation")
    equal(bundle["receipts"], [stop], "Wallet receipt history")

    receipt = _context_record(effect["effect_receipt"], context, "b", EFFECT_SCHEMA,
                              action=action, subject=True)
    equal(receipt["scope"], "GITHUB_PR_MERGE_ONLY", "effect scope")
    equal(receipt["status"], "MERGE_CONFIRMED", "effect status")
    equal(receipt["target"], target.to_record(), "effect target")
    equal(receipt["admission_receipt_hash"], record_hash(b), "admission hash")
    equal(receipt["frontier_receipt_hash"], record_hash(frontier), "frontier hash")
    equal(receipt["before"], _read(path / "after_a.json"), "effect before")
    _snapshot(receipt["before"], target, merged=False, state="open", initial=True)
    after = _read(path / "after_b.json")
    equal(receipt["after"], after, "effect after")
    _snapshot(after, target, merged=True, state="closed")
    response = _read(path / "merge_response.json")
    equal(response["merged"], True, "merge response")
    sha = response["sha"]
    require(HEX40.fullmatch(sha) is not None, "merge SHA invalid")
    equal(receipt["merge_commit_sha"], sha, "effect merge SHA")
    equal(after["merge_commit_sha"], sha, "remote merge SHA")
    commit = receipt["merge_commit"]
    equal(commit["sha"], sha, "commit SHA")
    parents = commit["parents"]
    require(type(parents) is list and len(parents) == 2 and
            all(isinstance(p,str) and HEX40.fullmatch(p) is not None for p in parents),
            "merge parents invalid")
    equal(parents[1], target.head_sha, "reviewed head parent")
    equal(commit["base_drift"], parents[0] != target.base_sha, "base drift")

    for phase, closure, mandate, sequence, hashes, fenced in [
        ("a", closure_a, "grant-a", 2, [], 1),
        ("b", closure_b, "grant-b", 4, [record_hash(receipt)], 0)]:
        _context_record(closure, context, phase, CLOSURE_SCHEMA)
        equal(closure["scope"], "RECEIVER_CONTROLLED_GITHUB_PR_MERGE", "closure scope")
        equal(closure["target"], target.to_record(), "closure target")
        equal(closure["head_sequence"], sequence, "closure sequence")
        equal(closure["head_hash"], record_hash(events[sequence-1]), "closure head")
        equal(closure["status"], "EFFECT_CLOSED", "closure status")
        equal(closure["active_frontiers"], 0, "active frontiers")
        equal(closure["pending_fenced"], fenced, "fenced count")
        equal(closure["confirmed_effect_hashes"], hashes, "confirmed effects")
        equal(closure["unattributed_merge_observations"], [], "unattributed effects")
        require(parse_time(closure["closed_at"]) >=
                parse_time(events[sequence-1]["issued_at"]), "closure precedes revocation")
    require(parse_time(closure_b["closed_at"]) >= parse_time(receipt["confirmed_at"]),
            "closure precedes effect")
    for phase, admission in [("a",a),("b",b)]:
        require(parse_time(admission["decided_at"]) <=
                parse_time(events[1 if phase=="a" else 3]["issued_at"]),
                "admission after revocation")
    equal(_read(path / "merge_requests_after_a.json"), 0, "pre-dispatch merge count")
    equal(_read(path / "merge_requests.json"), 1, "merge count")
    equal(_read(path / "wallet_receipt_count.json"), 2, "Wallet receipt count")
    equal(_read(path / "errors.json"), [], "experiment errors")
    require(_read(path / "closure_blocked_during_ack.json") is True, "ack hold invalid")
    timing = _read(path / "timing.json")
    fields = ("ack_observed_ns","revocation_requested_ns","closure_requested_ns",
              "ack_released_ns","closure_returned_ns")
    require(all(type(timing.get(k)) is int and timing[k] >= 0 for k in fields),
            "timing invalid")
    require(all(timing[a] <= timing[b] for a,b in zip(fields,fields[1:])),
            "frontier ordering invalid")
    return {"principal": context["principal"]["principal_id"],
            "receivers": [context["receivers"][p]["gate_id"] for p in ("a","b")],
            "merge_sha": sha}


def run(output: Path, *, context: Mapping[str, Any] | None = None,
        self_attested_fixture: bool = False) -> dict[str, Any]:
    output = Path(output)
    result = _packet(output, top=True)
    digest = _hash((output / "result.json").read_bytes())
    historical = digest == FROZEN_RESULT_SHA256
    source_status = _source_closure(result, historical=historical)
    repaired = output / "repaired"
    repair_result = _packet(repaired, top=False)
    equal(repair_result["base_commit"], result["base_commit"], "base commit")
    equal(repair_result["transport"], "fixture", "nested transport")
    equal(_read(repaired / "experiment_id.json"), "PROVIDER-EFFECT-001", "nested experiment")
    equal(_read(repaired / "verdict.json"), repair_result["verdict"], "nested verdict")
    equal(_read(repaired / "transport.json"), repair_result["transport"], "nested transport value")
    equal(_read(repaired / "base_commit.json"), result["base_commit"], "nested base")
    if context is None:
        if historical:
            context = _historical_context()
            trust = "PINNED_HISTORICAL_CONTEXT"
        elif self_attested_fixture:
            context = fixture_context(output)
            trust = "SELF_ATTESTED_FIXTURE_CONTEXT"
        else:
            raise RuntimeError("EXPECTED_CONTEXT_REQUIRED")
    else:
        trust = "CALLER_SUPPLIED_CONTEXT"
    context = json.loads(json.dumps(context))
    target = _target(_read(repaired / "target.json"), context["target"])
    for phase in ("a","b"):
        receiver = context["receivers"][phase]
        equal(receiver["gate_id"], "provider-effect-" + phase, "receiver identity")
        equal(receiver["mandate_id"], "grant-" + phase, "receiver mandate")
        equal(receiver["subject_id"], "agent-" + phase, "receiver subject")
    require(context["receivers"]["a"]["gate_public_key"] !=
            context["receivers"]["b"]["gate_public_key"], "receiver keys not distinct")
    _check_unsafe(_read(output / "unsafe-control.json"), context, target)
    bindings = _check_repaired(repaired, context)
    return {"verdict": result["verdict"], "result_hash": record_hash(result),
            "checks": len(result["checks"]), "source_files": len(result["source_sha256"]),
            "evidence_files": len(result["evidence_sha256"]), "context_trust": trust,
            "source_status": source_status, "bindings": bindings,
            "claim_boundary": result["claim_boundary"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--self-attested-fixture", action="store_true",
                        help="Explicitly allow identity bootstrap from a fresh disposable fixture")
    parser.add_argument("--context", type=Path,
                        help="Caller-trusted principal, receiver and target expectations")
    args = parser.parse_args()
    context = _read(args.context) if args.context else None
    print(json.dumps(run(args.output, context=context,
                         self_attested_fixture=args.self_attested_fixture),
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
