"""Selected public transaction evidence; never export inputs or key files."""
from __future__ import annotations

import hashlib
import json

FORMAT = "openline.exchange.selected-evidence.v1"


def export_selected(exchange, job_ids):
    """Read durable records without changing them. Selection is explicit.

    Original signed bodies are retained, not re-signed/redacted. This format
    supports the public text_digest fixture only. It is data minimization,
    not cryptographic selective disclosure or a private-history guarantee.
    """
    source = exchange.chome / "jobs.json"
    source_bytes = source.read_bytes() if source.exists() else b"{}"
    jobs = json.loads(source_bytes)
    offers = exchange.read_json("offers.json", {})
    selected = []
    for job_id in dict.fromkeys(job_ids):
        job = jobs[job_id]
        if job["agreement"]["service"] != "text_digest":
            raise ValueError("only public text_digest evidence is supported")
        selected.append({
            "job_id": job_id,
            "agreement": {"record": job["agreement"],
                          "signature": job["agreement_signature"]},
            "offer": offers[job["agreement"]["offer_id"]],
            "submission": job.get("submission"),
            "verdict": job.get("verdict"),
            "settlement": job.get("settlement"),
        })
    profiles = [{k: listing.get(k) for k in (
        "listing_id", "seller_id", "seller_name", "service", "capability",
        "price", "currency", "standing", "offer_id")}
        for listing in exchange.registry.all()]
    return {"format": FORMAT,
            "warning": "LOCAL CONTROLLED WORKERS; SIM_USD simulated, no real payment",
            "source": {"record_file": "commission/jobs.json",
                       "sha256": hashlib.sha256(source_bytes).hexdigest()},
            "profiles": profiles, "jobs": selected}
