from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openline_wallet.crypto import record_hash, verify_record
from openline_wallet.receiver import ROUTE_RECEIPT_SCHEMA


ALLOWED_VERDICTS = {
    "ROUTE_AUTHORITY_REPAIR_PASS",
    "ROUTE_AUTHORITY_REPAIR_FAILED",
    "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()

    result = load(args.artifact_dir / "result.json")
    witness = load(args.artifact_dir / "delivery-witness.json")
    summary = load(args.artifact_dir / "summary.json")

    assert result["schema"] == "openline.route_authority_gate.result.v1"
    assert result["experiment_id"] == "ROUTE-AUTHORITY-GATE-001"
    assert result["verdict"] in ALLOWED_VERDICTS
    assert witness == result["delivery_witness"]

    body = dict(result)
    claimed_hash = body.pop("result_hash")
    assert record_hash(body) == claimed_hash
    assert summary["result_hash"] == claimed_hash
    assert summary["verdict"] == result["verdict"]
    assert summary["unauthorized_protected_delivery"] == result["unauthorized_protected_delivery"]

    cases = result["cases"]
    blocked_direct = cases["blocked_direct_read"]
    clean_action = cases["clean_sender_action"]
    clean_route = cases["clean_sender_route"]
    source_read = cases["source_protected_read"]
    blocked_send = cases["source_generic_send_to_blocked"]
    blocked_route = cases["source_route_to_blocked"]
    allowed_send = cases["source_generic_send_to_allowed"]
    allowed_route = cases["source_route_to_allowed"]

    for route in (clean_route, blocked_route, allowed_route):
        assert route["schema"] == ROUTE_RECEIPT_SCHEMA
        ok, reason = verify_record(route, expected_public_key=route["gate_public_key"])
        assert ok is True, reason

    controls_ok = (
        blocked_direct["decision"] == "STOPPED"
        and blocked_direct["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
        and clean_action["decision"] == "ALLOWED"
        and clean_route["decision"] == "ALLOWED"
        and source_read["decision"] == "ALLOWED"
        and cases["source_carried_scopes_after_read"] == ["records:read:payroll"]
        and blocked_send["decision"] == "ALLOWED"
        and allowed_send["decision"] == "ALLOWED"
        and allowed_route["decision"] == "ALLOWED"
        and any(row["kind"] == "benign-clean-sender" for row in witness)
        and any(row["kind"] == "protected-transform-to-authorized" for row in witness)
    )
    unauthorized = any(
        row["kind"] == "protected-transform-to-unauthorized"
        for row in witness
    )
    assert unauthorized == result["unauthorized_protected_delivery"]

    if not controls_ok:
        expected = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"
    elif blocked_route["decision"] == "STOPPED" and blocked_route["reason_codes"] == [
        "RECIPIENT_LACKS_SOURCE_AUTHORITY"
    ] and not unauthorized:
        expected = "ROUTE_AUTHORITY_REPAIR_PASS"
    else:
        expected = "ROUTE_AUTHORITY_REPAIR_FAILED"

    assert result["verdict"] == expected
    print(json.dumps({"verified": True, "verdict": expected, "result_hash": claimed_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
