"""JOINT-WORK-SPECKIT-001 — zero-provider Spec Kit substrate proof.

The experiment composes a pinned GitHub Spec Kit project definition with
existing OpenLine Wallet task authority and Airlock protected acceptance.
It does not add an orchestrator, planner, policy engine, shared memory layer,
or live-provider path.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

EXPERIMENT_ID = "JOINT-WORK-SPECKIT-001"
VERDICT_PASS = "SCRIPTED_SPECKIT_ARM_PASS_LIVE_NOT_RUN"
VERDICT_FAIL = "SCRIPTED_SPECKIT_ARM_FAIL"

WALLET_BASE = "1d9a8e0514efd55fe9df337f8cfe2ae75f44dc9b"
AIRLOCK_SHA = "3ef34fb0100516e458cb362a7448c78a72da097b"
SPECKIT_SHA = "d848fb4e18f44640ad6b42e60a280551ee90cdce"
SPECKIT_TEMPLATE_BLOBS = {
    "templates/constitution-template.md": "a4670ff46919b276a4c9663b4ca51830108fcfc0",
    "templates/spec-template.md": "ceb28776215a098e977650ac090c785dcbf53651",
    "templates/plan-template.md": "36f2eab16880bac670fe43cbe7ef2b9bc8c3aa2f",
    "templates/tasks-template.md": "7fff087cc5a3c51a889d865fd9126607a032d233",
}

HERE = Path(__file__).resolve().parent
PREREG = HERE / "prereg.json"
SUBSTRATE = HERE / "substrate"

SPEC_ARTIFACTS = [
    ".specify/memory/constitution.md",
    "specs/001-signed-webhook/spec.md",
    "specs/001-signed-webhook/plan.md",
    "specs/001-signed-webhook/tasks.md",
    "specs/001-signed-webhook/contracts/webhook.md",
]

PROTECTED = [
    ".gitignore",
    ".airlock/config.json",
    *SPEC_ARTIFACTS,
    "tests/test_producer_checkpoint.py",
    "tests/test_producer.py",
    "tests/test_receiver.py",
    "tests/test_convergence.py",
]
PRODUCER_ALLOWED = ["src/producer.py"]
RECEIVER_ALLOWED = ["src/receiver.py"]

BASE_PRODUCER = '''\
"""Producer half of the frozen Spec Kit fixture."""


def canonical_payload(payload):
    raise NotImplementedError("T101 required")


def signature_header(payload, secret):
    raise NotImplementedError("T101 required")


def build_request(event, resource_id, amount_cents, timestamp, nonce, secret):
    raise NotImplementedError("T102 required")
'''

BASE_RECEIVER = '''\
"""Receiver half of the frozen Spec Kit fixture."""


class WebhookReceiver:
    def __init__(self, secret):
        self.secret = secret
        self.seen_nonces = set()

    def receive(self, body, signature_header, now):
        raise NotImplementedError("T201 required")
'''

PRODUCER_T101_IMPL = '''\
from __future__ import annotations
import hashlib
import hmac
import json


def canonical_payload(payload):
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def signature_header(payload, secret):
    body = canonical_payload(payload)
    return "v1=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def build_request(event, resource_id, amount_cents, timestamp, nonce, secret):
    raise NotImplementedError("T102 required")
'''

PRODUCER_T102_IMPL = '''\
from __future__ import annotations
import hashlib
import hmac
import json


def canonical_payload(payload):
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def signature_header(payload, secret):
    body = canonical_payload(payload)
    return "v1=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def build_request(event, resource_id, amount_cents, timestamp, nonce, secret):
    if not isinstance(event, str) or not event:
        raise ValueError("event")
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("resource_id")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents < 0:
        raise ValueError("amount_cents")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("timestamp")
    if not isinstance(nonce, str) or not nonce.isascii() or not 16 <= len(nonce) <= 64:
        raise ValueError("nonce")
    payload = {
        "event": event,
        "resource_id": resource_id,
        "amount_cents": amount_cents,
        "timestamp": timestamp,
        "nonce": nonce,
    }
    body = canonical_payload(payload)
    return body, "v1=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
'''

RECEIVER_T201_IMPL = '''\
from __future__ import annotations
import hashlib
import hmac
import json
import re

_REQUIRED = {"amount_cents", "event", "nonce", "resource_id", "timestamp"}
_SIG = re.compile(r"^v1=[0-9a-f]{64}$")


class WebhookReceiver:
    def __init__(self, secret):
        self.secret = secret
        self.seen_nonces = set()

    def receive(self, body, signature_header, now):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return 400, {"error": "bad_payload"}
        if not isinstance(payload, dict) or set(payload) != _REQUIRED:
            return 400, {"error": "bad_payload"}
        if (
            not isinstance(payload["event"], str) or not payload["event"]
            or not isinstance(payload["resource_id"], str) or not payload["resource_id"]
            or isinstance(payload["amount_cents"], bool)
            or not isinstance(payload["amount_cents"], int)
            or payload["amount_cents"] < 0
            or isinstance(payload["timestamp"], bool)
            or not isinstance(payload["timestamp"], int)
            or not isinstance(payload["nonce"], str)
            or not payload["nonce"].isascii()
            or not 16 <= len(payload["nonce"]) <= 64
        ):
            return 400, {"error": "bad_payload"}
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if canonical != body:
            return 400, {"error": "bad_payload"}
        if abs(now - payload["timestamp"]) > 300:
            return 422, {"error": "stale_timestamp"}
        if not isinstance(signature_header, str) or _SIG.fullmatch(signature_header) is None:
            return 401, {"error": "invalid_signature"}
        expected = "v1=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature_header, expected):
            return 401, {"error": "invalid_signature"}
        nonce = payload["nonce"]
        if nonce in self.seen_nonces:
            return 409, {"error": "replay"}
        self.seen_nonces.add(nonce)
        return 200, {"status": "accepted"}
'''

CHECKPOINT_TEST = r'''from __future__ import annotations
import hashlib
import hmac
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from producer import build_request, canonical_payload, signature_header

payload = {
    "event": "invoice.paid",
    "resource_id": "inv_42",
    "amount_cents": 1250,
    "timestamp": 1_700_000_000,
    "nonce": "nonce-000000000001",
}
expected = (
    b'{"amount_cents":1250,"event":"invoice.paid",'
    b'"nonce":"nonce-000000000001","resource_id":"inv_42",'
    b'"timestamp":1700000000}'
)
secret = b"public-fixture-secret"
assert canonical_payload(payload) == expected
expected_sig = "v1=" + hmac.new(secret, expected, hashlib.sha256).hexdigest()
assert signature_header(payload, secret) == expected_sig
try:
    build_request(
        payload["event"], payload["resource_id"], payload["amount_cents"],
        payload["timestamp"], payload["nonce"], secret,
    )
except NotImplementedError:
    pass
else:
    raise AssertionError("T101 checkpoint overreached into T102")
'''

PRODUCER_TEST = r'''from __future__ import annotations
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from producer import build_request

secret = b"public-fixture-secret"
body, header = build_request(
    "invoice.paid", "inv_42", 1250, 1_700_000_000,
    "nonce-000000000001", secret,
)
payload = json.loads(body)
assert payload["event"] == "invoice.paid"
assert payload["resource_id"] == "inv_42"
assert payload["amount_cents"] == 1250
assert payload["timestamp"] == 1_700_000_000
# Deliberately local-only: validates nonce shape, not caller-value preservation.
assert isinstance(payload["nonce"], str)
assert payload["nonce"].isascii()
assert 16 <= len(payload["nonce"]) <= 64
assert body == json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode("utf-8")
assert re.fullmatch(r"v1=[0-9a-f]{64}", header)
assert hmac.compare_digest(
    header, "v1=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
)
'''

RECEIVER_TEST = r'''from __future__ import annotations
import hashlib
import hmac
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from receiver import WebhookReceiver

secret = b"public-fixture-secret"
receiver = WebhookReceiver(secret)
now = 1_700_000_100
payload = {
    "event": "invoice.paid",
    "resource_id": "inv_42",
    "amount_cents": 1250,
    "timestamp": 1_700_000_000,
    "nonce": "nonce-000000000001",
}
body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
sig = "v1=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
status, out = receiver.receive(body, sig, now)
assert status == 200 and out == {"status": "accepted"}
status, out = receiver.receive(body, sig, now)
assert status == 409 and out == {"error": "replay"}
fresh = dict(payload, nonce="nonce-000000000002")
fresh_body = json.dumps(fresh, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
status, out = receiver.receive(fresh_body, "v1=" + ("0" * 64), now)
assert status == 401 and out == {"error": "invalid_signature"}
stale = dict(payload, nonce="nonce-000000000003", timestamp=now - 301)
stale_body = json.dumps(stale, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
stale_sig = "v1=" + hmac.new(secret, stale_body, hashlib.sha256).hexdigest()
status, out = receiver.receive(stale_body, stale_sig, now)
assert status == 422 and out == {"error": "stale_timestamp"}
status, out = receiver.receive(b"not-json", "v1=" + ("0" * 64), now)
assert status == 400 and out == {"error": "bad_payload"}
'''

CONVERGENCE_TEST = r'''from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from producer import build_request
from receiver import WebhookReceiver

secret = b"public-fixture-secret"
receiver = WebhookReceiver(secret)
now = 1_700_000_100

body1, sig1 = build_request(
    "invoice.paid", "inv_42", 1250, 1_700_000_000,
    "nonce-000000000001", secret,
)
body2, sig2 = build_request(
    "invoice.paid", "inv_43", 1500, 1_700_000_001,
    "nonce-000000000002", secret,
)
status1, out1 = receiver.receive(body1, sig1, now)
status2, out2 = receiver.receive(body2, sig2, now)
assert status1 == 200 and out1 == {"status": "accepted"}
assert status2 == 200 and out2 == {"status": "accepted"}, (
    "FR-004 violated: distinct caller nonces must remain distinct end to end"
)
replay_status, replay_out = receiver.receive(body1, sig1, now)
assert replay_status == 409 and replay_out == {"error": "replay"}
'''

AIRLOCK_CONFIG = {
    "schema": "joint-work-speckit.airlock-config.v1",
    "protected": PROTECTED,
    "ordinary_checks": [
        [sys.executable, "-B", "tests/test_producer.py"],
        [sys.executable, "-B", "tests/test_receiver.py"],
    ],
    "acceptance_checks": [[sys.executable, "-B", "tests/test_convergence.py"]],
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command(
    argv: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = command(["git", "-C", str(repo), *args], Path.cwd(), timeout=45)
    if check and proc.returncode != 0:
        raise RuntimeError(f"GIT_FAILED:{' '.join(args)}:{proc.stderr.strip()}")
    return proc.stdout.strip()


def changed_paths(repo: Path) -> list[str]:
    proc = command(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        Path.cwd(), timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError("GIT_STATUS_FAILED:" + proc.stderr.strip())
    paths: list[str] = []
    for row in proc.stdout.splitlines():
        if not row.strip():
            continue
        if len(row) >= 4 and row[2] == " ":
            paths.append(row[3:])
        else:
            parts = row.split(maxsplit=1)
            if len(parts) == 2:
                paths.append(parts[1])
    return sorted(set(paths))


def commit_scope(repo: Path, allowed: list[str], message: str) -> str:
    paths = changed_paths(repo)
    if paths != sorted(allowed):
        raise RuntimeError("WORKER_SCOPE_VIOLATION:" + ",".join(paths))
    git(repo, "add", *allowed)
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "JOINT-WORK-SPECKIT worker")
    env.setdefault("GIT_AUTHOR_EMAIL", "worker@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "JOINT-WORK-SPECKIT worker")
    env.setdefault("GIT_COMMITTER_EMAIL", "worker@example.invalid")
    proc = subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("WORKER_COMMIT_FAILED:" + proc.stderr.strip())
    return git(repo, "rev-parse", "HEAD")


def run_check(repo: Path, rel: str) -> dict[str, Any]:
    proc = command(
        [sys.executable, "-B", rel],
        repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=45,
    )
    return {
        "path": rel,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "stdout": proc.stdout[-5000:],
        "stderr": proc.stderr[-5000:],
    }


def prereg() -> dict[str, Any]:
    return json.loads(PREREG.read_text(encoding="utf-8"))


def substrate_digests() -> tuple[dict[str, str], str]:
    digests = {path: sha256_file(SUBSTRATE / path) for path in SPEC_ARTIFACTS}
    root = sha256_bytes(
        json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digests, root


def validate_substrate_shape() -> dict[str, Any]:
    frozen = prereg()["spec_kit_freeze"]
    digests, root = substrate_digests()
    if digests != frozen["artifact_sha256"]:
        raise RuntimeError("SPEC_KIT_ARTIFACT_DIGEST_MISMATCH")
    if root != frozen["root_digest"]:
        raise RuntimeError("SPEC_KIT_ROOT_DIGEST_MISMATCH")

    constitution = (SUBSTRATE / ".specify/memory/constitution.md").read_text(encoding="utf-8")
    spec = (SUBSTRATE / "specs/001-signed-webhook/spec.md").read_text(encoding="utf-8")
    plan = (SUBSTRATE / "specs/001-signed-webhook/plan.md").read_text(encoding="utf-8")
    tasks = (SUBSTRATE / "specs/001-signed-webhook/tasks.md").read_text(encoding="utf-8")
    contract = (SUBSTRATE / "specs/001-signed-webhook/contracts/webhook.md").read_text(encoding="utf-8")

    required_fragments = {
        "constitution": ["## Core Principles", "## Governance", "Exact Nonce Preservation"],
        "spec": ["## User Scenarios & Testing", "## Requirements", "**FR-004**", "## Success Criteria"],
        "plan": ["## Constitution Check", "## Project Structure"],
        "tasks": [
            "T101 [P] [US1]",
            "T102 [US1]",
            "T201 [P] [US2]",
            "T301 [US3]",
        ],
        "contract": ["## Canonical Payload", "## Signature", "## Receiver Results"],
    }
    docs = {
        "constitution": constitution,
        "spec": spec,
        "plan": plan,
        "tasks": tasks,
        "contract": contract,
    }
    for name, fragments in required_fragments.items():
        for fragment in fragments:
            if fragment not in docs[name]:
                raise RuntimeError(f"SPEC_KIT_ARTIFACT_SHAPE_MISSING:{name}:{fragment}")
    return {"artifact_sha256": digests, "root_digest": root, "shape": "PASS"}


def validate_spec_kit_source(spec_kit_root: Path) -> dict[str, Any]:
    spec_kit_root = spec_kit_root.resolve()
    head = git(spec_kit_root, "rev-parse", "HEAD")
    dirty = git(spec_kit_root, "status", "--porcelain", "--untracked-files=all")
    if head != SPECKIT_SHA or dirty:
        raise RuntimeError(f"SPEC_KIT_SOURCE_PIN_FAILURE:{head}:{bool(dirty)}")
    blobs: dict[str, str] = {}
    for path, expected in SPECKIT_TEMPLATE_BLOBS.items():
        actual = git(spec_kit_root, "hash-object", path)
        if actual != expected:
            raise RuntimeError(f"SPEC_KIT_TEMPLATE_BLOB_MISMATCH:{path}:{actual}")
        blobs[path] = actual
    return {"head": head, "template_git_blobs": blobs, "clean": True}


def wallet_source_pin() -> str:
    repo = HERE.parents[1]
    head = git(repo, "rev-parse", "HEAD")
    proc = command(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", WALLET_BASE, head],
        Path.cwd(), timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"WALLET_BASE_NOT_ANCESTOR:{WALLET_BASE}:{head}")
    return head


def airlock_source_pin() -> str:
    import airlock

    repo = Path(airlock.__file__).resolve().parents[2]
    head = git(repo, "rev-parse", "HEAD")
    dirty = git(repo, "status", "--porcelain", "--untracked-files=all")
    if head != AIRLOCK_SHA or dirty:
        raise RuntimeError(f"AIRLOCK_SOURCE_PIN_FAILURE:{head}:{bool(dirty)}")
    return head


def initialize_fixture(root: Path) -> dict[str, Any]:
    repo = root / "signed-webhook-speckit"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "JOINT-WORK-SPECKIT owner")
    git(repo, "config", "user.email", "owner@example.invalid")

    for path in SPEC_ARTIFACTS:
        dst = repo / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SUBSTRATE / path, dst)

    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / ".airlock").mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.pytest_cache/\n", encoding="utf-8")
    (repo / "src/producer.py").write_text(BASE_PRODUCER, encoding="utf-8")
    (repo / "src/receiver.py").write_text(BASE_RECEIVER, encoding="utf-8")
    (repo / "tests/test_producer_checkpoint.py").write_text(CHECKPOINT_TEST, encoding="utf-8")
    (repo / "tests/test_producer.py").write_text(PRODUCER_TEST, encoding="utf-8")
    (repo / "tests/test_receiver.py").write_text(RECEIVER_TEST, encoding="utf-8")
    (repo / "tests/test_convergence.py").write_text(CONVERGENCE_TEST, encoding="utf-8")
    write_json(repo / ".airlock/config.json", AIRLOCK_CONFIG)

    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Freeze Spec Kit project and owner acceptance")
    base = git(repo, "rev-parse", "HEAD")
    frozen_hashes = {path: sha256_file(repo / path) for path in PROTECTED}
    return {
        "repo": repo,
        "base": base,
        "frozen_hashes": frozen_hashes,
    }


def task_action(task_id: str, spec_root: str) -> str:
    return f"speckit:{task_id}:{spec_root[:24]}"


class AuthoritySession:
    def __init__(self, root: Path, spec_root: str, base: str):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from openline_wallet.crypto import public_key_hex, sign_record
        from openline_wallet.receiver import ReferenceGate
        from openline_wallet.wallet import Wallet

        self.public_key_hex = public_key_hex
        self.sign_record = sign_record
        self.owner = Wallet.create(root / "owner-wallet", label=EXPERIMENT_ID)
        self.gate = ReferenceGate("joint-work-speckit-001")
        self.gate.pin_principal(self.owner.principal_id, self.owner.root_public_key)
        self.keys = {
            "worker-a": Ed25519PrivateKey.generate(),
            "worker-b": Ed25519PrivateKey.generate(),
            "worker-a2": Ed25519PrivateKey.generate(),
        }
        self.spec_root = spec_root
        self.base = base
        self.actions = {
            "T101": task_action("T101", spec_root),
            "T102": task_action("T102", spec_root),
            "T201": task_action("T201", spec_root),
        }
        self.mandates: dict[str, str] = {}
        self.bundles: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.agreement = sign_record(
            {
                "schema": "openline.joint-work-speckit.owner-agreement.v1",
                "experiment_id": EXPERIMENT_ID,
                "spec_kit_pin": SPECKIT_SHA,
                "spec_root_digest": spec_root,
                "fixture_base": base,
                "task_bindings": {
                    "worker-a": ["T101", "T102"],
                    "worker-b": ["T201"],
                    "owner": ["T301"],
                },
                "protected": PROTECTED,
                "airlock_pin": AIRLOCK_SHA,
            },
            self.owner.root_key,
        )

    def initial_grants(self) -> dict[str, Any]:
        expires = datetime.now(timezone.utc) + timedelta(minutes=20)
        a = self.owner.grant(
            subject_id="worker-a",
            subject_public_key=self.public_key_hex(self.keys["worker-a"]),
            scopes=[self.actions["T101"], self.actions["T102"]],
            expires_at=expires,
            mandate_id="speckit_worker_a_initial",
        )
        b = self.owner.grant(
            subject_id="worker-b",
            subject_public_key=self.public_key_hex(self.keys["worker-b"]),
            scopes=[self.actions["T201"]],
            expires_at=expires,
            mandate_id="speckit_worker_b_initial",
        )
        self.mandates["worker-a"] = a["data"]["mandate_id"]
        self.mandates["worker-b"] = b["data"]["mandate_id"]
        bundle = self.owner.export_bundle()
        self.gate.admit_bundle(bundle)
        self.bundles.append(bundle)
        return {"worker_a": a, "worker_b": b, "bundle": bundle}

    def authorize(
        self,
        subject: str,
        task_id: str,
        *,
        bundle: Mapping[str, Any] | None = None,
        mandate_id: str | None = None,
        action_override: str | None = None,
    ) -> dict[str, Any]:
        from openline_wallet.receiver import create_presentation

        action = action_override or self.actions[task_id]
        use_bundle = dict(bundle or self.bundles[-1])
        challenge = self.gate.issue_challenge(
            principal_id=self.owner.principal_id,
            subject_id=subject,
            action=action,
        )
        presentation = create_presentation(
            bundle=use_bundle,
            mandate_id=mandate_id or self.mandates[subject],
            subject_id=subject,
            subject_key=self.keys[subject],
            action=action,
            receiver_challenge=challenge,
        )
        receipt = self.gate.evaluate(presentation, expected_action=action)
        self.receipts.append(receipt)
        self.owner.add_receipt(receipt)
        return receipt

    def revoke_worker_a(self) -> dict[str, Any]:
        revocation = self.owner.revoke("worker-a", reason="WORKER_REPLACED")
        bundle = self.owner.export_bundle()
        self.gate.admit_bundle(bundle)
        self.bundles.append(bundle)
        stopped = self.authorize(
            "worker-a", "T102", bundle=bundle, mandate_id=self.mandates["worker-a"]
        )
        if stopped["decision"] != "STOPPED" or "MANDATE_REVOKED" not in stopped["reason_codes"]:
            raise RuntimeError("REVOKED_WORKER_A_NOT_STOPPED")
        return {"revocation": revocation, "bundle": bundle, "stopped": stopped}

    def grant_successor(self, handoff_hash: str) -> dict[str, Any]:
        successor_action = f"speckit:T102:{self.spec_root[:12]}:handoff:{handoff_hash[:16]}"
        event = self.owner.grant(
            subject_id="worker-a2",
            subject_public_key=self.public_key_hex(self.keys["worker-a2"]),
            scopes=[successor_action],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            mandate_id="speckit_worker_a2_successor",
        )
        self.mandates["worker-a2"] = event["data"]["mandate_id"]
        bundle = self.owner.export_bundle()
        self.gate.admit_bundle(bundle)
        self.bundles.append(bundle)
        allowed = self.authorize(
            "worker-a2",
            "T102",
            bundle=bundle,
            action_override=successor_action,
        )
        if allowed["decision"] != "ALLOWED":
            raise RuntimeError("SUCCESSOR_NOT_ALLOWED")
        return {"event": event, "bundle": bundle, "receipt": allowed, "action": successor_action}


def scripted_edit(repo: Path, role: str) -> dict[str, Any]:
    if role == "worker-a":
        rel, content, delay = PRODUCER_ALLOWED[0], PRODUCER_T101_IMPL, 0.30
    elif role == "worker-b":
        rel, content, delay = RECEIVER_ALLOWED[0], RECEIVER_T201_IMPL, 0.35
    elif role == "worker-a2":
        rel, content, delay = PRODUCER_ALLOWED[0], PRODUCER_T102_IMPL, 0.20
    else:
        raise ValueError(role)
    started = time.monotonic_ns()
    time.sleep(delay)
    (repo / rel).write_text(content, encoding="utf-8")
    ended = time.monotonic_ns()
    return {
        "role": role,
        "mode": "scripted",
        "started_monotonic_ns": started,
        "ended_monotonic_ns": ended,
        "changed_paths": changed_paths(repo),
        "provider_spend_usd": 0.0,
    }


def overlap_ns(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    return max(
        0,
        min(int(a["ended_monotonic_ns"]), int(b["ended_monotonic_ns"]))
        - max(int(a["started_monotonic_ns"]), int(b["started_monotonic_ns"])),
    )


def protected_hashes_at(repo: Path, commit: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in PROTECTED:
        raw = subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{path}"])
        values[path] = sha256_bytes(raw)
    return values


def airlock_evaluate(repo: Path, base: str, candidate: str) -> dict[str, Any]:
    from airlock.sandbox import WorktreeSandbox
    from airlock.sieve import protected_files_check, run_checks

    changed = [
        line
        for line in git(repo, "diff", "--name-only", f"{base}..{candidate}").splitlines()
        if line.strip()
    ]
    protected = protected_files_check(changed, PROTECTED)
    ordinary: dict[str, Any] = {"status": "NOT_RUN"}
    acceptance: dict[str, Any] = {"status": "NOT_RUN"}
    if protected["status"] == "PASS":
        with WorktreeSandbox(repo, candidate, prefix="speckit-eval-") as wt:
            ordinary = run_checks(
                wt,
                [
                    [sys.executable, "-B", "tests/test_producer.py"],
                    [sys.executable, "-B", "tests/test_receiver.py"],
                ],
                timeout=45,
                kind="ordinary",
            )
            if ordinary["status"] == "PASS":
                acceptance = run_checks(
                    wt,
                    [[sys.executable, "-B", "tests/test_convergence.py"]],
                    timeout=45,
                    kind="spec_kit_composition",
                )
    eligible = (
        protected["status"] == "PASS"
        and ordinary["status"] == "PASS"
        and acceptance["status"] == "PASS"
    )
    return {
        "candidate": candidate,
        "changed_paths": changed,
        "protected": protected,
        "ordinary": ordinary,
        "acceptance": acceptance,
        "status": "ELIGIBLE" if eligible else "REJECTED",
        "airlock_sha": AIRLOCK_SHA,
    }


def combine(repo: Path, base: str, commits: list[str], branch: str) -> str:
    from airlock.sandbox import WorktreeSandbox

    with WorktreeSandbox(repo, base, branch=branch, prefix="speckit-compose-") as wt:
        for commit in commits:
            proc = command(["git", "cherry-pick", commit], wt, timeout=45)
            if proc.returncode != 0:
                raise RuntimeError("COMPOSE_CHERRY_PICK_FAILED:" + (proc.stdout + proc.stderr)[-3000:])
        return git(wt, "rev-parse", "HEAD")


def make_negative(repo: Path, producer_final: str) -> tuple[str, dict[str, Any]]:
    from airlock.sandbox import WorktreeSandbox

    with WorktreeSandbox(
        repo,
        producer_final,
        branch="speckit/negative-producer",
        prefix="speckit-negative-",
    ) as wt:
        path = wt / PRODUCER_ALLOWED[0]
        text = path.read_text(encoding="utf-8")
        needle = '"nonce": nonce,'
        if needle not in text:
            raise RuntimeError("NEGATIVE_MUTATION_POINT_MISSING")
        path.write_text(text.replace(needle, '"nonce": "nonce-fixed-0001",', 1), encoding="utf-8")
        local = run_check(wt, "tests/test_producer.py")
        if local["status"] != "PASS":
            raise RuntimeError("NEGATIVE_CONTROL_NOT_LOCALLY_GREEN")
        commit = commit_scope(wt, PRODUCER_ALLOWED, "Negative control: collapse caller nonce")
    return commit, local


def finish_manifest(output: Path) -> None:
    manifest: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.json":
            manifest[str(path.relative_to(output))] = sha256_file(path)
    write_json(output / "SHA256SUMS.json", manifest)


def reproduce(output: Path, spec_kit_root: Path) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    substrate = validate_substrate_shape()
    upstream = validate_spec_kit_source(spec_kit_root)
    wallet_head = wallet_source_pin()
    airlock_head = airlock_source_pin()

    with tempfile.TemporaryDirectory(prefix="joint-work-speckit-001-") as temp_s:
        root = Path(temp_s)
        fixture = initialize_fixture(root)
        repo: Path = fixture["repo"]
        authority = AuthoritySession(root / "authority", substrate["root_digest"], fixture["base"])
        grants = authority.initial_grants()

        cross_scope = authority.authorize("worker-a", "T201")
        if cross_scope["decision"] != "STOPPED" or "ACTION_OUTSIDE_MANDATE" not in cross_scope["reason_codes"]:
            raise RuntimeError("CROSS_SCOPE_PROBE_FAILED")

        a_auth = authority.authorize("worker-a", "T101")
        b_auth = authority.authorize("worker-b", "T201")
        if a_auth["decision"] != "ALLOWED" or b_auth["decision"] != "ALLOWED":
            raise RuntimeError("INITIAL_TASK_AUTHORITY_FAILED")

        from airlock.sandbox import WorktreeSandbox

        with WorktreeSandbox(
            repo, fixture["base"], branch="speckit/worker-a", prefix="speckit-worker-a-"
        ) as a_wt, WorktreeSandbox(
            repo, fixture["base"], branch="speckit/worker-b", prefix="speckit-worker-b-"
        ) as b_wt:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fa = pool.submit(scripted_edit, a_wt, "worker-a")
                fb = pool.submit(scripted_edit, b_wt, "worker-b")
                a_info = fa.result()
                b_info = fb.result()

            overlap = overlap_ns(a_info, b_info)
            if overlap <= 0:
                raise RuntimeError("PARALLEL_PROGRESS_NOT_OBSERVED")
            if a_info["changed_paths"] != PRODUCER_ALLOWED:
                raise RuntimeError("WORKER_A_SCOPE_VIOLATION")
            if b_info["changed_paths"] != RECEIVER_ALLOWED:
                raise RuntimeError("WORKER_B_SCOPE_VIOLATION")

            a_checkpoint = run_check(a_wt, "tests/test_producer_checkpoint.py")
            a_full_before = run_check(a_wt, "tests/test_producer.py")
            b_local = run_check(b_wt, "tests/test_receiver.py")
            if a_checkpoint["status"] != "PASS" or a_full_before["status"] != "FAIL":
                raise RuntimeError("T101_CHECKPOINT_NOT_DISCRIMINATING")
            if b_local["status"] != "PASS":
                raise RuntimeError("T201_LOCAL_CHECK_FAILED")

            a_commit = commit_scope(a_wt, PRODUCER_ALLOWED, "T101 accepted producer checkpoint")
            b_commit = commit_scope(b_wt, RECEIVER_ALLOWED, "T201 receiver implementation")

        from openline_wallet.crypto import record_hash, sign_record, verify_record

        checkpoint_blob = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{a_commit}:{PRODUCER_ALLOWED[0]}"]
        )
        handoff = sign_record(
            {
                "schema": "openline.joint-work-speckit.handoff.v1",
                "experiment_id": EXPERIMENT_ID,
                "spec_kit_pin": SPECKIT_SHA,
                "spec_root_digest": substrate["root_digest"],
                "checkpoint_task": "T101",
                "checkpoint_commit": a_commit,
                "checkpoint_file_sha256": sha256_bytes(checkpoint_blob),
                "worker_a_receipt_hash": record_hash(a_auth),
                "unresolved_task": "T102",
                "worker_b_checkpoint_commit": b_commit,
                "private_provider_chat_transferred": False,
                "provider_credentials_transferred": False,
            },
            authority.owner.root_key,
        )
        handoff_hash = record_hash(handoff)
        handoff_valid, handoff_reason = verify_record(
            handoff, expected_public_key=authority.owner.root_public_key
        )
        if handoff_valid is not True:
            raise RuntimeError("HANDOFF_SIGNATURE_INVALID:" + str(handoff_reason))

        revocation = authority.revoke_worker_a()
        successor_auth = authority.grant_successor(handoff_hash)

        with WorktreeSandbox(
            repo, a_commit, branch="speckit/worker-a2", prefix="speckit-worker-a2-"
        ) as a2_wt:
            a2_info = scripted_edit(a2_wt, "worker-a2")
            if a2_info["changed_paths"] != PRODUCER_ALLOWED:
                raise RuntimeError("SUCCESSOR_SCOPE_VIOLATION")
            a2_local = run_check(a2_wt, "tests/test_producer.py")
            if a2_local["status"] != "PASS":
                raise RuntimeError("T102_SUCCESSOR_LOCAL_CHECK_FAILED")
            a2_commit = commit_scope(a2_wt, PRODUCER_ALLOWED, "T102 successor completion")

        if command(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", a_commit, a2_commit],
            Path.cwd(), timeout=30,
        ).returncode != 0:
            raise RuntimeError("SUCCESSOR_NOT_DESCENDANT_OF_T101_CHECKPOINT")

        negative_commit, negative_local = make_negative(repo, a2_commit)
        negative_candidate = combine(
            repo,
            fixture["base"],
            [a_commit, a2_commit, negative_commit, b_commit],
            "speckit/integration-negative",
        )
        negative_eval = airlock_evaluate(repo, fixture["base"], negative_candidate)
        if not (
            negative_local["status"] == "PASS"
            and negative_eval["ordinary"]["status"] == "PASS"
            and negative_eval["acceptance"]["status"] == "FAIL"
            and negative_eval["status"] == "REJECTED"
        ):
            raise RuntimeError("NEGATIVE_COMPOSITION_NOT_REJECTED")

        valid_candidate = combine(
            repo,
            fixture["base"],
            [a_commit, a2_commit, b_commit],
            "speckit/integration-valid",
        )
        valid_eval = airlock_evaluate(repo, fixture["base"], valid_candidate)
        if valid_eval["status"] != "ELIGIBLE":
            raise RuntimeError("VALID_COMPOSITION_NOT_ACCEPTED")

        valid_frozen = protected_hashes_at(repo, valid_candidate)
        negative_frozen = protected_hashes_at(repo, negative_candidate)
        frozen_unchanged = fixture["frozen_hashes"] == valid_frozen == negative_frozen
        if not frozen_unchanged:
            raise RuntimeError("FROZEN_SPEC_OR_ACCEPTANCE_CHANGED")

        result = {
            "schema": "openline.joint-work-speckit-001.result.v1",
            "experiment_id": EXPERIMENT_ID,
            "mode": "scripted_zero_provider",
            "verdict": VERDICT_PASS,
            "prereg_sha256": sha256_file(PREREG),
            "pins": {
                "wallet_preregistered_base": WALLET_BASE,
                "wallet_runtime_head": wallet_head,
                "airlock": airlock_head,
                "spec_kit": upstream["head"],
                "spec_kit_template_git_blobs": upstream["template_git_blobs"],
            },
            "spec_kit": {
                "artifact_sha256": substrate["artifact_sha256"],
                "root_digest": substrate["root_digest"],
                "shape_validation": substrate["shape"],
                "frozen_unchanged_through_both_compositions": frozen_unchanged,
            },
            "parallel": {
                "overlap_ns": overlap,
                "worker_a": a_info,
                "worker_b": b_info,
            },
            "authority": {
                "owner_agreement": authority.agreement,
                "initial_grants": {
                    "worker_a": grants["worker_a"],
                    "worker_b": grants["worker_b"],
                },
                "cross_scope_probe": cross_scope,
                "worker_a_T101": a_auth,
                "worker_b_T201": b_auth,
                "worker_a_revocation": revocation["revocation"],
                "post_revocation_T102": revocation["stopped"],
                "successor_mandate": successor_auth["event"],
                "successor_T102": successor_auth["receipt"],
            },
            "checkpoints": {
                "worker_a_T101_commit": a_commit,
                "worker_b_T201_commit": b_commit,
                "successor_T102_commit": a2_commit,
                "handoff": handoff,
                "handoff_hash": handoff_hash,
                "handoff_signature_verified": True,
                "successor_descends_from_T101": True,
            },
            "local_checks": {
                "T101_checkpoint": a_checkpoint,
                "T101_full_before_successor": a_full_before,
                "T201_receiver": b_local,
                "T102_successor": a2_local,
                "negative_producer": negative_local,
            },
            "negative_control": negative_eval,
            "valid_composition": valid_eval,
            "provider_calls": [],
            "provider_spend_usd": 0.0,
            "earned_claim": (
                "In a zero-provider scripted fixture using a pinned GitHub Spec Kit project definition, "
                "Wallet enforced separate task authority and revocation/successor handoff, while Airlock "
                "rejected a locally-green cross-task requirement violation and accepted the valid combined implementation."
            ),
            "nonclaims": prereg()["nonclaims"],
        }

        write_json(output / "result.json", result)
        write_json(output / "owner-agreement.json", authority.agreement)
        write_json(output / "handoff.json", handoff)
        write_json(
            output / "wallet-evidence.json",
            {
                "receipts": authority.receipts,
                "final_bundle": authority.bundles[-1],
            },
        )
        write_json(output / "negative-control.json", negative_eval)
        write_json(output / "valid-composition.json", valid_eval)
        write_json(output / "spec-kit-source.json", upstream)
        finish_manifest(output)
        return result


def self_test() -> None:
    data = prereg()
    assert data["experiment_id"] == EXPERIMENT_ID
    substrate = validate_substrate_shape()
    assert substrate["root_digest"] == data["spec_kit_freeze"]["root_digest"]
    tasks = (SUBSTRATE / "specs/001-signed-webhook/tasks.md").read_text(encoding="utf-8")
    assert re.search(r"T101 \[P\] \[US1\].*src/producer\.py", tasks)
    assert re.search(r"T201 \[P\] \[US2\].*src/receiver\.py", tasks)
    assert '"nonce": nonce,' in PRODUCER_T102_IMPL
    broken = PRODUCER_T102_IMPL.replace('"nonce": nonce,', '"nonce": "nonce-fixed-0001",', 1)
    assert '"nonce": "nonce-fixed-0001",' in broken
    assert SPECKIT_TEMPLATE_BLOBS == data["pins"]["spec_kit_template_git_blobs"]
    print("JOINT-WORK-SPECKIT-001 self-test: PASS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-kit-root")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    if not args.spec_kit_root or not args.output:
        parser.error("--spec-kit-root and --output are required")

    result = reproduce(Path(args.output).resolve(), Path(args.spec_kit_root).resolve())
    print(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "verdict": result["verdict"],
                "spec_root_digest": result["spec_kit"]["root_digest"],
                "parallel_overlap_ns": result["parallel"]["overlap_ns"],
                "provider_spend_usd": result["provider_spend_usd"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["verdict"] == VERDICT_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
