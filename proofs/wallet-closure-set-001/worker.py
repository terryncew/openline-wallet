"""Isolated test receiver. Private Gate keys never leave this process.

The trusted harness uses a private multiprocessing pipe for fault injection.
This is not a network API or a production receiver service.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, Thread
import traceback
from typing import Any

from openline_wallet.canonical import strict_json_load
from openline_wallet.closure_set import attest_closure
from openline_wallet.effect_closure import EffectClosure, EffectGate
from openline_wallet.errors import WalletError
from openline_wallet import effect_closure as effect_module


def worker_main(conn, directory: str, gate_id: str, principal: str, root_public: str) -> None:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    ledger = root / "effects.json"
    gate = EffectGate(gate_id)
    gate.pin_principal(principal, root_public)
    effects = EffectClosure(gate, ledger, root / "receipts")
    send_lock = Lock()
    write_release = Event()
    write_release.set()
    hold_release = {"value": None}
    original_write = effect_module.atomic_write_json
    threads: list[Thread] = []

    def send(value: dict[str, Any]) -> None:
        with send_lock:
            conn.send(value)

    def hooked_write(path, value, **kwargs):
        if Path(path) == ledger and isinstance(value, list) and value:
            if value[-1].get("release") == hold_release["value"] and not write_release.is_set():
                send({"event": "frontier_entered", "gate_id": gate_id})
                if not write_release.wait(15):
                    raise TimeoutError("test frontier hold timed out")
        return original_write(path, value, **kwargs)

    effect_module.atomic_write_json = hooked_write
    send({"event": "ready", "gate_id": gate_id, "gate_public_key": gate.public_key})

    def dispatch(message: dict[str, Any]) -> Any:
        op = message["op"]
        if op == "admit":
            return gate.admit_bundle(message["bundle"])
        if op == "challenge":
            return gate.issue_challenge(principal_id=principal, subject_id="agent-a", action="deploy:staging")
        if op == "prepare":
            return effects.prepare(message["presentation"], action="deploy:staging", release=message["release"])
        if op == "finish":
            return effects.finish(message["ticket"], action="deploy:staging", release=message["release"])
        if op == "close":
            return attest_closure(gate, effects, message["manifest"], message["request"], message["bundle"])
        if op == "ledger":
            return strict_json_load(ledger) if ledger.exists() else []
        if op == "hold_write":
            hold_release["value"] = message["release"]
            write_release.clear()
            return {"holding": hold_release["value"]}
        if op == "release_write":
            write_release.set()
            return {"released": True}
        raise WalletError("TEST_OPERATION_UNKNOWN")

    def perform(message: dict[str, Any]) -> None:
        try:
            result = dispatch(message)
            send({"id": message["id"], "ok": True, "result": result})
        except Exception as exc:
            send({"id": message["id"], "ok": False,
                  "error": exc.code if isinstance(exc, WalletError) else type(exc).__name__,
                  "traceback": traceback.format_exc()})

    try:
        while True:
            message = conn.recv()
            if message["op"] == "shutdown":
                write_release.set()
                send({"id": message["id"], "ok": True, "result": {"shutdown": True}})
                break
            if message["op"] in {"finish", "close"}:
                thread = Thread(target=perform, args=(message,), daemon=True)
                threads.append(thread)
                thread.start()
            else:
                perform(message)
    except EOFError:
        pass
    finally:
        write_release.set()
        for thread in threads:
            thread.join(timeout=3)
        effects.shutdown()
        conn.close()
