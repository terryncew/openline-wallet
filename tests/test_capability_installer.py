"""Regression controls for the capinstall preview, run against the CLI entry
point. Ports the discriminating controls from CAPABILITY-EXCHANGE-001:

  rejected package, substituted artifact, wrong buyer/version,
  altered acceptance policy, replay, revoked invocation.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CLI = [sys.executable, "-m", "openline_wallet.capability_installer.cli"]
REPO_SRC = Path(__file__).resolve().parents[1] / "src"


def run(home, *args, env_extra=None):
    env = dict(os.environ, CAPINSTALL_HOME=str(home))
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        CLI + list(args), env=env, capture_output=True, text=True, cwd=str(REPO_SRC.parent)
    )


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "caphome"
    r = run(h, "init")
    assert r.returncode == 0, r.stderr
    return h


def pkgdir_of(home):
    return home / "packages" / "example"


def package_hash_of(home):
    return next(iter(json.loads((home / "decisions.json").read_text())))


def test_full_flow(home):
    pkg = pkgdir_of(home)
    r = run(home, "inspect", str(pkg))
    assert r.returncode == 0, r.stderr
    info = json.loads("\n".join(r.stdout.splitlines()[: r.stdout.splitlines().index("}") + 1]))
    assert info["artifact_sha256"] == "80d6bb6ea5198cbbc4ec4fa300704fd1b1a7254062da461cd59b49ea43bfb44b"
    assert info["requested_permissions"] == []
    assert "signature valid: True" in r.stdout

    r = run(home, "accept", str(pkg))
    assert r.returncode == 0, r.stderr
    assert "buyer battery score: 10/12, buyer threshold: 10/12" in r.stdout
    ph = package_hash_of(home)

    r = run(home, "import", str(pkg))
    assert r.returncode == 0, r.stderr
    assert (home / "lineage" / ph / "symptom_summarizer.py").exists()

    r = run(home, "invoke", ph, "w01_mixed_eval_majority")
    assert r.returncode == 0, r.stderr
    receipts = list((home / "receipts" / "invocations").glob("*.json"))
    assert len(receipts) == 1
    body = json.loads(receipts[0].read_text())
    assert body["receipt"]["package_hash"] == ph
    # receipt is signed
    from openline_wallet.crypto import verify_record

    ok, _ = verify_record(body["signature"])
    assert ok

    r = run(home, "settle", ph, "--demo")
    assert r.returncode == 0, r.stderr
    assert "SIMULATED settlement" in r.stdout
    ledger = json.loads((home / "settlement.json").read_text())
    assert ledger["balances"] == {"buyer": 9750, "seller": 250}
    assert len(ledger["transfers"]) == 1

    r = run(home, "settle", ph, "--demo")
    assert r.returncode == 2
    assert "ALREADY_SETTLED" in r.stderr

    r = run(home, "revoke", ph)
    assert r.returncode == 0, r.stderr

    r = run(home, "invoke", ph, "w02_all_ok")
    assert r.returncode == 2
    assert "MANDATE_REVOKED" in r.stderr


def test_rejected_package(home):
    pkg = home / "packages" / "bad"
    pkg.mkdir(parents=True)
    bad = (
        "VERSION = 'bad'\n\n"
        "def summarize(probe_results, absent_files):\n"
        "    return {'version': VERSION}\n"
    )
    (pkg / "symptom_summarizer.py").write_text(bad)
    manifest = {
        "schema": "capability-installer.manifest.v1",
        "name": "symptom_summarizer",
        "version": "bad",
        "artifact": "symptom_summarizer.py",
        "artifact_sha256": "x",
        "requested_permissions": [],
        "signatures": [],
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest))
    r = run(home, "accept", str(pkg))
    assert r.returncode == 2
    assert "REJECTED" in r.stdout
    r = run(home, "import", str(pkg))
    assert r.returncode == 2
    assert "IMPORT_REFUSED" in r.stderr
    r = run(home, "settle", "no-such-hash", "--demo")
    assert r.returncode == 2


def test_substituted_artifact(home):
    pkg = pkgdir_of(home)
    r = run(home, "accept", str(pkg))
    assert r.returncode == 0, r.stderr
    # seller swaps the artifact after acceptance
    (pkg / "symptom_summarizer.py").write_text(
        "VERSION='x'\ndef summarize(a, b):\n    return {'version': VERSION}\n"
    )
    r = run(home, "import", str(pkg))
    assert r.returncode == 2
    assert "IMPORT_REFUSED" in r.stderr


def test_wrong_version(home):
    pkg = pkgdir_of(home)
    r = run(home, "accept", str(pkg))
    assert r.returncode == 0, r.stderr
    man_path = pkg / "manifest.json"
    manifest = json.loads(man_path.read_text())
    manifest["version"] = "baseline-evil"
    man_path.write_text(json.dumps(manifest))
    r = run(home, "import", str(pkg))
    assert r.returncode == 2
    assert "IMPORT_REFUSED" in r.stderr  # package hash no longer matches acceptance


def test_altered_acceptance_policy(home, tmp_path):
    # tampered copy of the product source: battery code changed after pinning
    tampered = tmp_path / "tampered_src"
    shutil.copytree(REPO_SRC, tampered / "src")
    bat = tampered / "src" / "openline_wallet" / "capability_installer" / "battery.py"
    src = bat.read_text().replace("ACCURACY_BAR = 10 / 12", "ACCURACY_BAR = 1 / 12")
    bat.write_text(src)
    pkg = pkgdir_of(home)
    r = run(
        home,
        "accept",
        str(pkg),
        env_extra={"PYTHONPATH": str(tampered / "src")},
    )
    assert r.returncode == 2
    assert "BATTERY_INTEGRITY" in r.stderr


def test_settle_requires_acceptance_and_invocation(home):
    pkg = pkgdir_of(home)
    r = run(home, "accept", str(pkg))
    assert r.returncode == 0, r.stderr
    ph = package_hash_of(home)
    r = run(home, "settle", ph, "--demo")
    assert r.returncode == 2
    assert "INVOCATION_REQUIRED" in r.stderr
