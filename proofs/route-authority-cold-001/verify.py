from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openline_wallet.crypto import record_hash


ALLOWED_VERDICTS = {
    "ROUTE_AUTHORITY_HOLE_REPRODUCED",
    "NOT_REPRODUCED_CURRENT_GATE_BLOCKED",
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

    assert result["schema"] == "openline.route_authority_cold.result.v1"
    assert result["experiment_id"] == "ROUTE-AUTHORITY-COLD-001"
    assert result["verdict"] in ALLOWED_VERDICTS
    assert isinstance(witness, list)
    assert witness == result["delivery_witness"]

    body = dict(result)
    claimed_hash = body.pop("result_hash")
    assert record_hash(body) == claimed_hash
    assert summary["result_hash"] == claimed_hash
    assert summary["verdict"] == result["verdict"]
    assert summary["protected_payload_hash"] == result["protected_payload_hash"]
    assert summary["protected_delivered_to_agent_b"] == result["protected_delivered_to_agent_b"]

    cases = result["cases"]
    direct = cases["direct_b_protected_read"]
    source = cases["source_a_protected_read"]
    benign = cases["benign_generic_relay"]
    protected = cases["protected_generic_relay"]
    payload_control = cases["payload_specific_negative_control"]

    controls_ok = (
        direct["decision"] == "STOPPED"
        and direct["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
        and source["decision"] == "ALLOWED"
        and benign["decision"] == "ALLOWED"
        and payload_control["decision"] == "STOPPED"
        and payload_control["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
        and any(row["delivery_kind"] == "benign-control" for row in witness)
    )
    protected_delivered = any(
        row["recipient"] == "agent-b"
        and row["payload_hash"] == result["protected_payload_hash"]
        and row["origin_scope"] == "data:read:payments-research"
        for row in witness
    )
    assert protected_delivered == result["protected_delivered_to_agent_b"]

    if not controls_ok:
        expected = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"
    elif protected["decision"] == "STOPPED" and not protected_delivered:
        expected = "NOT_REPRODUCED_CURRENT_GATE_BLOCKED"
    elif protected["decision"] == "ALLOWED" and protected_delivered:
        expected = "ROUTE_AUTHORITY_HOLE_REPRODUCED"
    else:
        expected = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"

    assert result["verdict"] == expected
    print(json.dumps({"verified": True, "verdict": expected, "result_hash": claimed_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
