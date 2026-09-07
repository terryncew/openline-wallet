"""One receiver-controlled GitHub PR merge, with durable uncertainty.

This reference owns exactly one PR/head/base-branch capability. It does not
control other GitHub writers, merge queues, Actions, or downstream effects.
An ambiguous mutation is never retried. Reopening a journal never restores
execution capability. The local journal and signing key are trusted inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import quote, unquote
import os
import re
import sqlite3

from .canonical import canonical_json, strict_json_loads
from .clock import as_utc, isoformat, utc_now
from .crypto import record_hash, sha256_hex, sign_record, verify_record
from .effect_closure import EffectGate, _acquire_writer, _release_writer
from .errors import WalletError
from .wallet import DECISION_AUTHORITY, WALLET_POLICY_AUTHORITY

ACTION_SCHEMA = "openline.wallet.github_merge_action.v1"
EFFECT_SCHEMA = "openline.wallet.github_merge_effect.v1"
CLOSURE_SCHEMA = "openline.wallet.github_merge_closure.v1"
INTENT_SCHEMA = "openline.wallet.github_merge_intent.v1"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PATH = re.compile(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pulls/([0-9]+)(/merge)?")
_REPOSITORY_PATH = re.compile(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_BRANCH_PATH = re.compile(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/branches/([A-Za-z0-9._%/-]+)")
_COMMIT_PATH = re.compile(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/git/commits/([0-9a-f]{40})")
_TERMINAL = {"CONFIRMED", "STOPPED", "OBSERVED_MERGED_UNATTRIBUTED"}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise WalletError(code)


def _repo(value: str) -> str:
    _require(isinstance(value, str) and _REPO.fullmatch(value) is not None,
             "GITHUB_REPOSITORY_INVALID")
    _require(all(part not in {".", ".."} and not part.endswith(".git")
                 for part in value.split("/")), "GITHUB_REPOSITORY_INVALID")
    return value.lower()


def _sha(value: object, code: str = "GITHUB_SHA_INVALID") -> str:
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None, code)
    return value


def merge_action(repository: str, number: int, head_sha: str, *,
                 repository_id: int, base_ref: str) -> str:
    """Bind the owner, immutable repository identity, PR, head, and base name.

    The moving base SHA is intentionally not an authority constraint: GitHub's
    merge endpoint has a conditional head but no conditional base parameter.
    """
    repository = _repo(repository)
    _require(type(repository_id) is int and repository_id > 0, "GITHUB_REPOSITORY_ID_INVALID")
    _require(type(number) is int and 0 < number <= 2**31 - 1, "GITHUB_PR_NUMBER_INVALID")
    _sha(head_sha, "GITHUB_HEAD_SHA_INVALID")
    _require(isinstance(base_ref, str) and _REF.fullmatch(base_ref) is not None
             and not any(x in {"", ".", ".."} for x in base_ref.split("/")), "GITHUB_BASE_REF_INVALID")
    return "github:merge:" + sha256_hex(canonical_json({
        "repository": repository, "repository_id": repository_id, "number": number,
        "head_sha": head_sha, "base_ref": base_ref,
    }))


@dataclass(frozen=True)
class MergeTarget:
    repository: str
    repository_id: int
    number: int
    head_sha: str
    base_ref: str
    base_sha: str

    def __post_init__(self) -> None:
        merge_action(self.repository, self.number, self.head_sha,
                     repository_id=self.repository_id, base_ref=self.base_ref)
        _sha(self.base_sha, "GITHUB_BASE_SHA_INVALID")
        _require(self.repository == self.repository.lower(), "GITHUB_REPOSITORY_INVALID")

    @property
    def action(self) -> str:
        return merge_action(self.repository, self.number, self.head_sha,
                            repository_id=self.repository_id, base_ref=self.base_ref)

    def to_record(self) -> dict[str, Any]:
        return {"schema": ACTION_SCHEMA, "repository": self.repository,
                "repository_id": self.repository_id, "number": self.number,
                "head_sha": self.head_sha, "base_ref": self.base_ref,
                "base_sha": self.base_sha, "action": self.action}


class GitHubHTTPError(WalletError):
    def __init__(self, status: int, request_id: str | None = None):
        self.status, self.request_id = status, request_id
        super().__init__("GITHUB_HTTP_ERROR", str(status))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    """Official REST host, fixed endpoints, no redirects or automatic retries."""
    def __init__(self, token: str, *, api_root: str = "https://api.github.com", timeout: float = 20.0):
        _require(isinstance(token, str) and bool(token) and "\n" not in token and "\r" not in token,
                 "GITHUB_TOKEN_REQUIRED")
        _require(api_root == "https://api.github.com", "GITHUB_API_ROOT_INVALID")
        _require(type(timeout) in (int, float) and 0 < timeout <= 60, "GITHUB_TIMEOUT_INVALID")
        self._token, self.timeout = token, timeout
        self._opener = build_opener(_NoRedirect)

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        match = _PATH.fullmatch(path)
        commit = _COMMIT_PATH.fullmatch(path)
        repository = _REPOSITORY_PATH.fullmatch(path)
        branch = _BRANCH_PATH.fullmatch(path)
        _require(any(x is not None for x in (match, commit, repository, branch)), "GITHUB_PATH_INVALID")
        if match:
            _repo(match[1] + "/" + match[2])
            _require((method == "GET" and match[4] is None and body is None) or
                     (method == "PUT" and match[4] == "/merge" and isinstance(body, Mapping)
                      and set(body) == {"sha", "merge_method"} and body["merge_method"] == "merge"
                      and isinstance(body["sha"], str) and _SHA.fullmatch(body["sha"]) is not None),
                     "GITHUB_METHOD_INVALID")
        else:
            parsed = commit or repository or branch
            _repo(parsed[1] + "/" + parsed[2])
            if branch:
                name = unquote(branch[3])
                _require(_REF.fullmatch(name) is not None and quote(name, safe="") == branch[3]
                         and not any(x in {"", ".", ".."} for x in name.split("/")), "GITHUB_PATH_INVALID")
            _require(method == "GET" and body is None, "GITHUB_METHOD_INVALID")
        data = canonical_json(body) if body is not None else None
        req = Request("https://api.github.com" + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + self._token,
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2026-03-10",
                               "Content-Type": "application/json",
                               "User-Agent": "openline-wallet-provider-effect-001"})
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                _require(len(raw) <= 2 * 1024 * 1024, "GITHUB_RESPONSE_TOO_LARGE")
                result = strict_json_loads(raw.decode("utf-8"))
                _require(isinstance(result, dict), "GITHUB_RESPONSE_INVALID")
                return result
        except HTTPError as exc:
            # Every mutation error, including a 4xx, has an uncertain outcome.
            raise GitHubHTTPError(exc.code, exc.headers.get("X-GitHub-Request-Id")) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WalletError("GITHUB_TRANSPORT_UNCERTAIN", type(exc).__name__) from exc

    def repository(self, repository: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{_repo(repository)}")

    def branch(self, repository: str, base_ref: str) -> dict[str, Any]:
        _require(isinstance(base_ref, str) and _REF.fullmatch(base_ref) is not None,
                 "GITHUB_BASE_REF_INVALID")
        return self.request("GET", f"/repos/{_repo(repository)}/branches/{quote(base_ref, safe='')}")

    def pr(self, target: MergeTarget) -> dict[str, Any]:
        return self.request("GET", f"/repos/{target.repository}/pulls/{target.number}")

    def merge(self, target: MergeTarget) -> dict[str, Any]:
        return self.request("PUT", f"/repos/{target.repository}/pulls/{target.number}/merge",
                            {"sha": target.head_sha, "merge_method": "merge"})

    def commit(self, target: MergeTarget, sha: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{target.repository}/git/commits/{_sha(sha)}")


def _snapshot(pr: Mapping[str, Any], target: MergeTarget) -> dict[str, Any]:
    try:
        repo = pr["base"]["repo"]
        result = {"repository": repo["full_name"], "repository_id": repo["id"],
                  "number": pr["number"], "state": pr["state"], "merged": pr["merged"],
                  "head_sha": pr["head"]["sha"], "base_ref": pr["base"]["ref"],
                  "base_sha": pr["base"]["sha"], "merge_commit_sha": pr.get("merge_commit_sha")}
    except (KeyError, TypeError) as exc:
        raise WalletError("GITHUB_PR_RESPONSE_INVALID") from exc
    _require(type(result["repository_id"]) is int and type(result["number"]) is int and
             type(result["merged"]) is bool and result["state"] in {"open", "closed"} and
             result["repository"].lower() == target.repository and
             result["repository_id"] == target.repository_id and result["number"] == target.number and
             result["base_ref"] == target.base_ref, "GITHUB_PR_IDENTITY_MISMATCH")
    _sha(result["head_sha"])
    _sha(result["base_sha"])
    if result["merge_commit_sha"] is not None:
        _sha(result["merge_commit_sha"])
    return result


def _preflight(pr: Mapping[str, Any], target: MergeTarget) -> dict[str, Any]:
    state = _snapshot(pr, target)
    _require(state["state"] == "open" and state["merged"] is False and
             state["head_sha"] == target.head_sha and state["base_sha"] == target.base_sha,
             "GITHUB_PR_STATE_CHANGED")
    return state


def _merge_parents(client: GitHubClient, target: MergeTarget, sha: str) -> dict[str, Any]:
    """Verify the exact reviewed head is a parent of the resulting merge commit."""
    value = client.commit(target, sha)
    try:
        parents = [row["sha"] for row in value["parents"]]
        _require(value["sha"] == sha and len(parents) == 2 and
                 all(_SHA.fullmatch(x) is not None for x in parents) and
                 parents[1] == target.head_sha, "GITHUB_MERGE_COMMIT_INVALID")
    except (KeyError, TypeError, AttributeError) as exc:
        raise WalletError("GITHUB_MERGE_COMMIT_INVALID") from exc
    return {"sha": sha, "parents": parents, "base_drift": parents[0] != target.base_sha}


class GitHubMergeReceiver:
    """One fixed target, one Gate, and one durable single-writer journal.

    The local signing key and journal are trusted. On restart the journal is
    sealed against further execution. A completed or ambiguous mutation can
    never be sent again. Caller-supplied times are for deterministic tests only.
    """
    def __init__(self, gate: EffectGate, client: GitHubClient, target: MergeTarget,
                 journal_path: str | Path):
        _require(isinstance(gate, EffectGate), "EFFECT_GATE_REQUIRED")
        _require(len(gate._trusted_roots) == 1, "GITHUB_ONE_PRINCIPAL_REQUIRED")
        self.gate, self.client, self.target = gate, client, target
        self.path = Path(journal_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._fd = _acquire_writer(self.path.with_suffix(self.path.suffix + ".lock"))
        self._closed = False
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self._bind("target", target.to_record())
            self._bind("gate_id", gate.gate_id)
            self._bind("gate_public_key", gate.public_key)
            self._bind("principal_roots", dict(gate._trusted_roots))
            self._recovered = bool(self.db.execute("SELECT 1 FROM attempts LIMIT 1").fetchone())
            self._pending: dict[str, dict[str, Any]] = {}
            self._completed: dict[str, dict[str, Any]] = {}
            self._records()  # Validate every persisted signature before any operation.
            self._closure()  # A recovered certificate is also verified.
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            _release_writer(self._fd)
            raise

    def _bind(self, name: str, value: Any) -> None:
        serialized = canonical_json(value).decode("ascii")
        row = self.db.execute("SELECT value FROM meta WHERE name=?", (name,)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO meta VALUES (?,?)", (name, serialized))
        elif row[0] != serialized:
            raise WalletError("GITHUB_JOURNAL_BINDING_MISMATCH", name)

    def _store(self, record: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(record)
        body.pop("signature", None)
        body.pop("payload_hash", None)
        existing = self.db.execute("SELECT data FROM attempts WHERE id=?", (body["id"],)).fetchone()
        previous = strict_json_loads(existing[0]) if existing else None
        body["revision"] = previous["revision"] + 1 if previous else 1
        body["previous_hash"] = record_hash(previous) if previous else None
        body["gate_id"] = self.gate.gate_id
        body["gate_public_key"] = self.gate.public_key
        signed = sign_record(body, self.gate.gate_key)
        self.db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?)",
                        (body["id"], canonical_json(signed).decode("ascii")))
        return signed

    def _records(self) -> list[dict[str, Any]]:
        result = []
        for (raw,) in self.db.execute("SELECT data FROM attempts ORDER BY id"):
            row = strict_json_loads(raw)
            _require(isinstance(row, dict) and verify_record(row, expected_public_key=self.gate.public_key)[0]
                     and row.get("schema") == INTENT_SCHEMA and row.get("target") == self.target.to_record()
                     and row.get("gate_id") == self.gate.gate_id and row.get("gate_public_key") == self.gate.public_key
                     and row.get("status") in {"PREPARED", "IN_FLIGHT", "UNCERTAIN", *_TERMINAL},
                     "GITHUB_JOURNAL_INVALID")
            result.append(row)
        return result

    def _closure(self) -> dict[str, Any] | None:
        row = self.db.execute("SELECT value FROM meta WHERE name='closure'").fetchone()
        if row is None:
            return None
        value = strict_json_loads(row[0])
        _require(isinstance(value, dict) and verify_record(value, expected_public_key=self.gate.public_key)[0]
                 and value.get("schema") == CLOSURE_SCHEMA and value.get("target") == self.target.to_record(),
                 "GITHUB_JOURNAL_INVALID")
        return value

    def _assert_open(self) -> None:
        _require(not self._closed, "GITHUB_RECEIVER_CLOSED")

    def _assert_ready(self) -> None:
        self._assert_open()
        _require(self._closure() is None, "GITHUB_RECEIVER_CLOSED_BY_REVOCATION")
        _require(not self._recovered, "GITHUB_RECOVERY_REQUIRES_REVIEW")
        statuses = {row["status"] for row in self._records()}
        _require(not (statuses & {"IN_FLIGHT", "UNCERTAIN"}), "GITHUB_EFFECT_OUTCOME_UNRESOLVED")
        _require(not (statuses & {"CONFIRMED", "OBSERVED_MERGED_UNATTRIBUTED"}),
                 "GITHUB_TARGET_ALREADY_CONSUMED")

    def shutdown(self) -> None:
        with self._lock, self.gate._effect_lock:
            if not self._closed:
                self._closed = True
                self.db.close()
                _release_writer(self._fd)

    def _decision(self, admission: Mapping[str, Any], reason: str | None, now: datetime) -> dict[str, Any]:
        return self.gate._receipt(decision="STOPPED" if reason else "ALLOWED",
            reasons=[reason] if reason else [], principal_id=admission["principal_id"],
            mandate_id=admission["mandate_id"], subject_id=admission["subject_id"],
            action=self.target.action, presentation_hash=admission["presentation_hash"], now=now)

    def _stop(self, record: dict[str, Any], reason: str, now: datetime) -> dict[str, Any]:
        admission = record["admission"]
        receipt = self._decision(admission, reason, now)
        result = {"decision": "STOPPED", "reason_codes": [reason], "effect_applied": False,
                  "receipt": receipt, "admission_receipt": admission}
        record.update(status="STOPPED", frontier=receipt, result=result)
        self._store(record)
        self._pending.pop(record["id"], None)
        self._completed[record["id"]] = result
        return dict(result)

    def prepare(self, presentation: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock, self.gate._effect_lock:
            self._assert_ready()
            current = as_utc(now or utc_now())
            receipt = self.gate.evaluate(presentation, expected_action=self.target.action, now=current)
            if receipt["decision"] != "ALLOWED":
                return {"decision": "STOPPED", "receipt": receipt, "effect_applied": False,
                        "reason_codes": receipt["reason_codes"]}
            self._bind("grant", {"principal_id": receipt["principal_id"],
                                  "mandate_id": receipt["mandate_id"]})
            token = "gh_" + os.urandom(24).hex()
            row = {"id": token, "schema": INTENT_SCHEMA, "status": "PREPARED",
                   "target": self.target.to_record(), "admission": receipt,
                   "frontier": None, "before": None, "response": None, "after": None,
                   "merge_commit": None, "effect": None, "result": None,
                   "started_at": None, "completed_at": None}
            self._store(row)
            self._pending[token] = row
            return {"decision": "PREPARED", "ticket": token, "admission_receipt": receipt,
                    "effect_applied": False}

    def finish(self, ticket: str, *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock, self.gate._effect_lock:
            self._assert_open()
            if ticket in self._completed:
                return dict(self._completed[ticket])
            if self._closure() is not None:
                raise WalletError("GITHUB_RECEIVER_CLOSED_BY_REVOCATION")
            if self._recovered:
                raise WalletError("GITHUB_RECOVERY_REQUIRES_REVIEW")
            row = self._pending.get(ticket)
            if row is None:
                raise WalletError("GITHUB_TICKET_UNKNOWN")
            current = as_utc(now or utc_now())
            reason = self.gate._standing(row["admission"], current)
            if reason:
                return self._stop(row, reason, current)
            statuses = {r["status"] for r in self._records() if r["id"] != ticket}
            if statuses & {"IN_FLIGHT", "UNCERTAIN"}:
                return self._stop(row, "GITHUB_PRIOR_ATTEMPT_UNRESOLVED", current)
            if statuses & {"CONFIRMED", "OBSERVED_MERGED_UNATTRIBUTED"}:
                return self._stop(row, "GITHUB_TARGET_ALREADY_CONSUMED", current)
            try:
                before = _preflight(self.client.pr(self.target), self.target)
            except Exception as exc:
                reason = exc.code if isinstance(exc, WalletError) else "GITHUB_PREFLIGHT_UNAVAILABLE"
                return self._stop(row, reason, current)
            current = as_utc(now or utc_now())
            reason = self.gate._standing(row["admission"], current)
            if reason:
                return self._stop(row, reason, current)
            frontier = self._decision(row["admission"], None, current)
            row.update(status="IN_FLIGHT", frontier=frontier, before=before,
                       started_at=isoformat(current))
            self._store(row)  # A synchronous SQLite commit precedes the HTTP mutation.
            # The ticket is no longer merely prepared. Only the durable attempt
            # may resolve it; an ambiguous request must never become STOPPED.
            self._pending.pop(ticket, None)
            try:
                response = self.client.merge(self.target)
                _require(response.get("merged") is True and isinstance(response.get("sha"), str)
                         and _SHA.fullmatch(response["sha"]) is not None, "GITHUB_MERGE_RESPONSE_INVALID")
                row["response"] = {"merged": True, "sha": response["sha"]}
                self._store(row)
                after = _snapshot(self.client.pr(self.target), self.target)
                _require(after["merged"] is True and after["state"] == "closed" and after["head_sha"] == self.target.head_sha
                         and after["merge_commit_sha"] == response["sha"], "GITHUB_MERGE_NOT_RECONCILED")
                commit = _merge_parents(self.client, self.target, response["sha"])
                row["after"], row["merge_commit"] = after, commit
                effect = sign_record({"schema": EFFECT_SCHEMA, "gate_id": self.gate.gate_id,
                    "gate_public_key": self.gate.public_key, "principal_id": row["admission"]["principal_id"],
                    "mandate_id": row["admission"]["mandate_id"], "subject_id": row["admission"]["subject_id"],
                    "action": self.target.action, "target": self.target.to_record(),
                    "admission_receipt_hash": record_hash(row["admission"]),
                    "frontier_receipt_hash": record_hash(frontier), "attempt_id": ticket,
                    "merge_commit_sha": response["sha"], "merge_commit": commit,
                    "before": before, "after": after, "status": "MERGE_CONFIRMED",
                    "scope": "GITHUB_PR_MERGE_ONLY", "confirmed_at": isoformat(as_utc(now or utc_now())),
                    "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                    "decision_authority": DECISION_AUTHORITY}, self.gate.gate_key)
                result = {"decision": "ALLOWED", "reason_codes": [], "effect_applied": True,
                          "receipt": frontier, "admission_receipt": row["admission"], "effect_receipt": effect}
                row.update(status="CONFIRMED", effect=effect, result=result,
                           completed_at=effect["confirmed_at"])
                self._store(row)
                self._pending.pop(ticket, None)
                self._completed[ticket] = result
                return dict(result)
            except Exception:
                row["status"] = "UNCERTAIN"
                self._store(row)
                raise

    def reconcile(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Read-only provider reconciliation; no merge request is ever retried."""
        with self._lock, self.gate._effect_lock:
            self._assert_open()
            results = []
            for row in self._records():
                if row["status"] == "PREPARED" and self._recovered:
                    results.append(self._stop(row, "RECEIVER_RESTART_FENCED", as_utc(now or utc_now())))
                    continue
                if row["status"] not in {"IN_FLIGHT", "UNCERTAIN"}:
                    continue
                try:
                    after = _snapshot(self.client.pr(self.target), self.target)
                    row["after"] = after
                    if after["merged"] is True and after["state"] == "closed" and after["head_sha"] == self.target.head_sha:
                        commit = _merge_parents(self.client, self.target, after["merge_commit_sha"])
                        row["merge_commit"] = commit
                        row["status"] = "OBSERVED_MERGED_UNATTRIBUTED"
                    else:
                        row["status"] = "UNCERTAIN"
                    self._store(row)
                    results.append({"attempt_id": row["id"], "status": row["status"], "after": after})
                except Exception as exc:
                    results.append({"attempt_id": row["id"], "status": "UNCERTAIN",
                                    "reason": exc.code if isinstance(exc, WalletError) else "GITHUB_READ_FAILED"})
            return results

    def close(self, bundle: Mapping[str, Any], mandate_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock, self.gate._effect_lock:
            self._assert_open()
            existing = self._closure()
            if existing is not None:
                _require(existing["mandate_id"] == mandate_id, "GITHUB_CLOSURE_BINDING_MISMATCH")
                return existing
            current = as_utc(now or utc_now())
            admitted = self.gate.admit_bundle(bundle, now=current)
            principal = admitted["principal_id"]
            mandate = self.gate._admitted[principal].timeline.mandates.get(mandate_id)
            _require(mandate is not None and mandate["status"] == "REVOKED", "MANDATE_NOT_REVOKED")
            rows = self._records()
            _require(all(row["status"] not in {"IN_FLIGHT", "UNCERTAIN"} for row in rows),
                     "GITHUB_EFFECT_OUTCOME_UNRESOLVED")
            tracked = [row for row in rows if row["admission"]["principal_id"] == principal
                       and row["admission"]["mandate_id"] == mandate_id]
            pending = [row for row in self._pending.values() if row["admission"]["principal_id"] == principal
                       and row["admission"]["mandate_id"] == mandate_id]
            _require(bool(tracked or pending), "GITHUB_GRANT_NOT_TRACKED")
            for row in pending:
                _require(self.gate._standing(row["admission"], current) is not None, "GITHUB_CLOSURE_INCOMPLETE")
            # A prepared ticket never reached GitHub. Durable STOPPED records
            # make it impossible for a recovered owner to resurrect it.
            for row in pending:
                self._stop(row, "MANDATE_REVOKED", current)
            rows = self._records()
            _require(all(row["status"] in _TERMINAL for row in rows), "GITHUB_EFFECT_OUTCOME_UNRESOLVED")
            certificate = sign_record({"schema": CLOSURE_SCHEMA, "gate_id": self.gate.gate_id,
                "gate_public_key": self.gate.public_key, "principal_id": principal, "mandate_id": mandate_id,
                "target": self.target.to_record(), "head_sequence": admitted["head_sequence"],
                "head_hash": admitted["head_hash"], "status": "EFFECT_CLOSED",
                "scope": "RECEIVER_CONTROLLED_GITHUB_PR_MERGE", "pending_fenced": len(pending),
                "active_frontiers": 0, "confirmed_effect_hashes": [record_hash(row["effect"]) for row in tracked
                    if row["status"] == "CONFIRMED"],
                "unattributed_merge_observations": [row["id"] for row in tracked
                    if row["status"] == "OBSERVED_MERGED_UNATTRIBUTED"],
                "closed_at": isoformat(current), "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                "decision_authority": DECISION_AUTHORITY}, self.gate.gate_key)
            self.db.execute("INSERT INTO meta VALUES ('closure', ?)",
                            (canonical_json(certificate).decode("ascii"),))
            return certificate
