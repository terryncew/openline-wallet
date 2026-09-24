#!/usr/bin/env python3
"""Developer-preview marketplace UI for the OpenLine Agent Exchange.

Reads one exchange snapshot JSON and writes a self-contained
ui/index.html (no external assets, no network calls).

Usage:
    python3 ui/dashboard.py <snapshot.json> -o ui/index.html
    python3 ui/dashboard.py --demo -o ui/index.html   # inline sample snapshot

Stdlib only. $0 spend.
"""

import argparse
import html
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def esc(value):
    """HTML-escape anything."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def money(value):
    """Format a SIM_USD amount."""
    try:
        v = float(value)
        text = f"{v:g}"
    except (TypeError, ValueError):
        text = esc(value)
    return f"{text} SIM_USD (simulated)"


def short_hash(value, n=12):
    s = str(value or "")
    return s[:n] + ("..." if len(s) > n else "")


# ---------------------------------------------------------------------------
# Snapshot readers (defensive: every field via .get with a default)
# ---------------------------------------------------------------------------

def load_snapshot(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("snapshot root must be a JSON object")
    return data


def get_meta(snap):
    return snap.get("meta") or {}


def get_list(snap, key):
    value = snap.get(key)
    return value if isinstance(value, list) else []


def get_ledger(snap):
    value = snap.get("ledger")
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Timeline: the kernel loop, done/pending per step
# ---------------------------------------------------------------------------

# (step key, label, event names that satisfy the step)
STEPS = [
    ("need", "Need", {"NEED_POSTED"}),
    ("find", "Find", {"MATCH_REQUEST", "MATCH_RESPONSE", "FIND"}),
    ("contact", "Contact", {"CONTACT", "OFFER_LIST"}),
    ("offer", "Offer", {"AGREEMENT_PROPOSED"}),
    ("authorize", "Authorize", {"AGREEMENT_FROZEN"}),
    ("work", "Work", {"RESULT_DELIVERED"}),
    ("verify", "Verify", {"VERDICT"}),
    ("decide", "Accept / Refuse", {"VERDICT"}),
    ("settle", "Settle", {"SETTLED"}),
    ("receipt", "Receipt", {"RECEIPT"}),
    ("revoke", "Revoke", {"REVOKED", "SELLER_REVOKED", "AGENT_SWAPPED"}),
]


def _timeline_events(snap):
    out = []
    for entry in get_list(snap, "timeline"):
        if isinstance(entry, dict):
            out.append(entry)
    for msg in get_list(snap, "messages"):
        if isinstance(msg, dict) and msg.get("kind"):
            out.append({
                "t": msg.get("at", ""),
                "actor": msg.get("sender", ""),
                "event": str(msg.get("kind", "")).upper(),
                "detail": f"{msg.get('sender','')} -> {msg.get('recipient','')}",
            })
    for ev in get_list(snap, "events"):
        if isinstance(ev, dict) and ev.get("kind"):
            out.append({
                "t": "",
                "actor": "exchange",
                "event": str(ev.get("kind", "")).upper(),
                "detail": ev.get("detail", ""),
            })
    return out


def timeline_steps(snap):
    """Return [(key, label, done, t, actor, detail), ...] for the loop."""
    entries = _timeline_events(snap)
    steps = []
    for key, label, names in STEPS:
        hit = None
        for entry in entries:
            if str(entry.get("event", "")).upper() in names:
                hit = entry
                break
        if hit is not None:
            steps.append((key, label, True,
                          str(hit.get("t", "")), str(hit.get("actor", "")),
                          str(hit.get("detail", ""))))
        else:
            steps.append((key, label, False, "", "", ""))
    return steps


# ---------------------------------------------------------------------------
# Status chips
# ---------------------------------------------------------------------------

_JOB_CHIP = {
    "SETTLED": ("chip-settled", "SETTLED"),
    "ACCEPTED": ("chip-accepted", "ACCEPTED"),
    "REFUSED": ("chip-refused", "REFUSED"),
    "VERIFIED-REJECTED": ("chip-refused", "REFUSED"),
    "REJECTED": ("chip-refused", "REFUSED"),
    "REVOKED": ("chip-revoked", "REVOKED"),
    "PENDING-RELEASE": ("chip-pending", "PENDING-RELEASE"),
    "PENDING": ("chip-pending", "PENDING"),
}


def job_chip(status, verdict):
    """Normalize a job status/verdict into (css class, label)."""
    s = str(status or "").upper().replace("_", "-")
    v = str(verdict or "").lower()
    if s in _JOB_CHIP:
        return _JOB_CHIP[s]
    if v == "accepted":
        return ("chip-accepted", "ACCEPTED")
    if v == "rejected":
        return ("chip-refused", "REFUSED")
    return ("chip-pending", esc(status) if status else "UNKNOWN")


def standing_badge(standing):
    s = str(standing or "").lower()
    if s == "active":
        return '<span class="badge badge-active">ACTIVE</span>'
    return '<span class="badge badge-revoked">REVOKED</span>'


# ---------------------------------------------------------------------------
# Demo snapshot (inline sample, so the page can be eyeballed pre-kernel)
# ---------------------------------------------------------------------------

def demo_snapshot():
    return {
        "meta": {
            "title": "Agent Exchange Preview — developer snapshot",
            "generated_at": "2026-09-23T21:30:00Z",
            "home": "~/demo/agent-exchange-001 (local, file-backed)",
            "simulated": True,
        },
        "banner": ("LOCAL DEVELOPER PREVIEW — controlled agents, "
                   "SIM_USD simulated funds, not a functioning market."),
        "agents": [
            {"name": "buyer-agent", "role": "buyer agent",
             "principal": "principal-buyer-7f3a"},
            {"name": "buyer-agent-2", "role": "buyer agent (successor)",
             "principal": "principal-buyer-9c1d"},
            {"name": "seller-a", "role": "seller",
             "principal": "principal-seller-a-11aa"},
            {"name": "seller-b", "role": "seller",
             "principal": "principal-seller-b-22bb"},
            {"name": "seller-c", "role": "seller",
             "principal": "principal-seller-c-33cc"},
        ],
        "needs": [
            {"need_id": "need-abc1", "buyer": "buyer-agent",
             "description": "Weekly digest of this repo's open issues, "
                            "200 words max.",
             "keywords": ["digest", "weekly", "issues"],
             "max_price": 50, "service": "text_digest"},
            {"need_id": "need-abc2", "buyer": "buyer-agent-2",
             "description": "Changelog summary for release 0.4.0.",
             "keywords": ["changelog", "summary", "release"],
             "max_price": 30, "service": "text_digest"},
        ],
        "listings": [
            {"listing_id": "listing-a1", "seller_id": "principal-seller-a-11aa",
             "seller_name": "seller-a", "capability": "digest-report",
             "service": "text_digest", "artifact": "text_digest@1.0",
             "price": 40, "currency": "SIM_USD (simulated)",
             "terms": "results only; implementation retained by seller",
             "standing": "active", "revoked_reason": None},
            {"listing_id": "listing-b1", "seller_id": "principal-seller-b-22bb",
             "seller_name": "seller-b", "capability": "digest-report",
             "service": "text_digest", "artifact": "text_digest@2.0",
             "price": 60, "currency": "SIM_USD (simulated)",
             "terms": "results only; rush delivery available",
             "standing": "active", "revoked_reason": None},
            {"listing_id": "listing-c1", "seller_id": "principal-seller-c-33cc",
             "seller_name": "seller-c", "capability": "changelog-summary",
             "service": "text_digest", "artifact": "text_digest@1.0",
             "price": 25, "currency": "SIM_USD (simulated)",
             "terms": "results only; implementation retained by seller",
             "standing": "revoked",
             "revoked_reason": "seller withdrew the listing"},
        ],
        "timeline": [
            {"t": "0.0", "actor": "buyer-agent", "event": "NEED_POSTED",
             "detail": "need-abc1 posted: weekly digest, max 50 SIM_USD"},
            {"t": "1.2", "actor": "exchange", "event": "OFFER_LIST",
             "detail": "registry advertised 3 listings to buyer-agent"},
            {"t": "1.8", "actor": "buyer-agent", "event": "MATCH_RESPONSE",
             "detail": "2 candidates under budget; listing-a1 ranked first "
                       "(keyword overlap: digest, weekly)"},
            {"t": "2.1", "actor": "buyer-agent", "event": "CONTACT",
             "detail": "opened agreement discussion with seller-a"},
            {"t": "2.4", "actor": "seller-a", "event": "AGREEMENT_PROPOSED",
             "detail": "proposed digest-report at 40 SIM_USD, results only"},
            {"t": "2.9", "actor": "exchange", "event": "AGREEMENT_FROZEN",
             "detail": "agreement frozen; 40 SIM_USD reserved from "
                       "buyer-agent allowance"},
            {"t": "5.5", "actor": "seller-a", "event": "RESULT_DELIVERED",
             "detail": "delivered digest artifact (212 words)"},
            {"t": "6.0", "actor": "exchange", "event": "VERDICT",
             "detail": "receiver verdict on job-1a2b: accepted "
                       "(word count and keyword checks passed)"},
            {"t": "6.3", "actor": "exchange", "event": "SETTLED",
             "detail": "exactly-once settlement settlement-9f2c: "
                       "40 SIM_USD to seller-a"},
            {"t": "6.4", "actor": "exchange", "event": "RECEIPT",
             "detail": "receipt issued for job-1a2b "
                       "(hash 9f2ce4a1b7d3...)"},
            {"t": "9.0", "actor": "owner", "event": "AGENT_SWAPPED",
             "detail": "revoked buyer-agent mandate; delegated buyer-agent-2 "
                       "with a fresh allowance (no funds carried over)"},
            {"t": "10.2", "actor": "buyer-agent-2", "event": "NEED_POSTED",
             "detail": "need-abc2 posted: changelog summary, "
                       "max 30 SIM_USD"},
            {"t": "11.7", "actor": "exchange", "event": "AGREEMENT_FROZEN",
             "detail": "agreement frozen for job-5e6f with seller-a; "
                       "25 SIM_USD reserved"},
            {"t": "13.1", "actor": "seller-a", "event": "RESULT_DELIVERED",
             "detail": "delivered changelog artifact (640 words)"},
            {"t": "13.6", "actor": "exchange", "event": "VERDICT",
             "detail": "receiver verdict on job-5e6f: rejected "
                       "(artifact exceeded length cap)"},
            {"t": "15.0", "actor": "exchange", "event": "SELLER_REVOKED",
             "detail": "seller-c revoked listing-c1: "
                       "seller withdrew the listing"},
        ],
        "messages": [
            {"msg_id": "msg-01a2", "sender": "buyer-agent",
             "recipient": "exchange", "kind": "NEED_POSTED",
             "body": {"need_id": "need-abc1"}, "at": "t=0.0"},
            {"msg_id": "msg-02b3", "sender": "exchange",
             "recipient": "buyer-agent", "kind": "OFFER_LIST",
             "body": {"count": 3}, "at": "t=1.2"},
            {"msg_id": "msg-03c4", "sender": "seller-a",
             "recipient": "buyer-agent", "kind": "AGREEMENT_PROPOSED",
             "body": {"price": 40}, "at": "t=2.4"},
            {"msg_id": "msg-04d5", "sender": "seller-a",
             "recipient": "exchange", "kind": "RESULT_DELIVERED",
             "body": {"job_id": "job-1a2b"}, "at": "t=5.5"},
            {"msg_id": "msg-05e6", "sender": "exchange",
             "recipient": "buyer-agent", "kind": "VERDICT",
             "body": {"job_id": "job-1a2b", "verdict": "accepted"},
             "at": "t=6.0"},
            {"msg_id": "msg-06f7", "sender": "exchange",
             "recipient": "seller-a", "kind": "SETTLED",
             "body": {"job_id": "job-1a2b",
                      "settlement_id": "settlement-9f2c"},
             "at": "t=6.3"},
            {"msg_id": "msg-07a8", "sender": "exchange",
             "recipient": "buyer-agent", "kind": "RECEIPT",
             "body": {"job_id": "job-1a2b"}, "at": "t=6.4"},
        ],
        "jobs": [
            {"job_id": "job-1a2b", "need_id": "need-abc1", "buyer": "buyer-agent",
             "seller": "seller-a", "listing_id": "listing-a1", "amount": 40,
             "status": "SETTLED", "verdict": "accepted",
             "settlement_id": "settlement-9f2c"},
            {"job_id": "job-5e6f", "need_id": "need-abc2",
             "buyer": "buyer-agent-2", "seller": "seller-a",
             "listing_id": "listing-a1", "amount": 25,
             "status": "VERIFIED_REJECTED", "verdict": "rejected",
             "settlement_id": None},
            {"job_id": "job-7g8h", "need_id": "need-abc1", "buyer": "buyer-agent",
             "seller": "seller-b", "listing_id": "listing-b1", "amount": 60,
             "status": "PENDING-RELEASE", "verdict": "accepted",
             "settlement_id": None,
             "note": "verdict accepted; release interrupted before settle"},
        ],
        "receipts": [
            {"job_id": "job-1a2b", "settlement_id": "settlement-9f2c",
             "amount": 40, "hash": "9f2ce4a1b7d30566c8a1",
             "at": "t=6.4"},
        ],
        "ledger": {
            "balances": {
                "principal-buyer-7f3a": 9910,
                "principal-buyer-9c1d": 10000,
                "principal-seller-a-11aa": 10040,
                "principal-seller-b-22bb": 10000,
                "principal-seller-c-33cc": 10000,
            },
            "transfers": [
                {"from": "principal-buyer-7f3a",
                 "to": "principal-seller-a-11aa", "amount": 40,
                 "memo": "settlement-9f2c for job-1a2b"},
            ],
        },
        "events": [
            {"kind": "AGENT_SWAPPED",
             "detail": "owner revoked buyer-agent mandate; delegated "
                       "buyer-agent-2 with a fresh allowance"},
            {"kind": "SELLER_REVOKED",
             "detail": "seller-c revoked listing-c1: "
                       "seller withdrew the listing; existing receipts "
                       "unaffected"},
        ],
    }
# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

CSS = """
:root {
  --cream: #faf6ee;
  --paper: #fffdf8;
  --charcoal: #2b2b2b;
  --muted: #6f6a5e;
  --line: #e3dccb;
  --cobalt: #1f4fd8;
  --cobalt-soft: #e8eefc;
  --red-orange: #c9481c;
  --red-orange-soft: #fbeee6;
  --amber: #8a6d00;
  --amber-soft: #fdf6dd;
  --green: #2e7d32;
  --green-soft: #e9f4e9;
  --darkbar: #1c1a17;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  background: var(--cream); color: var(--charcoal);
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.5; font-size: 15px;
}
.banner {
  position: sticky; top: 0; z-index: 50;
  background: var(--darkbar); color: #f5f1e8;
  text-align: center; padding: 10px 16px;
  font-size: 13px; letter-spacing: 0.06em; font-weight: 600;
  border-bottom: 3px solid var(--cobalt);
}
.banner .dot { color: var(--cobalt); }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; }
h1 { font-size: 26px; margin: 8px 0 4px; }
h2 { font-size: 20px; margin: 36px 0 12px; padding-bottom: 6px;
     border-bottom: 2px solid var(--line); }
h3 { font-size: 16px; margin: 24px 0 8px; color: var(--charcoal); }
.meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
.meta code { background: #efe9da; padding: 1px 6px; border-radius: 4px; }
.sim-note { font-size: 13px; color: var(--muted); }

/* Tabs (anchor-linked, CSS only) */
.tabs { display: flex; gap: 0; margin: 20px 0 0; border-bottom: 2px solid var(--line); }
.tab {
  padding: 10px 26px; text-decoration: none; color: var(--charcoal);
  font-weight: 600; border: 2px solid transparent; border-bottom: none;
  border-radius: 8px 8px 0 0; margin-bottom: -2px;
}
.tab:hover { background: var(--cobalt-soft); }
.view { display: none; }
#buyer { display: block; }              /* default view */
#seller:target { display: block; }
#seller:target ~ #buyer { display: none; }
/* active-tab highlight (modern browsers support :has) */
.tab[href="#buyer"] { background: var(--cobalt-soft); border-color: var(--line); }
.view-wrap:has(#seller:target) .tab[href="#seller"] {
  background: var(--cobalt-soft); border-color: var(--line);
}
.view-wrap:has(#seller:target) .tab[href="#buyer"] {
  background: none; border-color: transparent;
}

/* Tables */
table { width: 100%; border-collapse: collapse; margin: 8px 0 20px;
        background: var(--paper); font-size: 14px; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { background: #f1ebdd; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.05em; color: var(--muted); }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 13px; }

/* Badges and chips */
.badge, .chip {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 700; letter-spacing: 0.04em; white-space: nowrap;
}
.badge-active { background: var(--green-soft); color: var(--green);
                border: 1px solid var(--green); }
.badge-revoked { background: var(--red-orange-soft); color: var(--red-orange);
                 border: 1px solid var(--red-orange); }
.chip-accepted { background: var(--green-soft); color: var(--green);
                 border: 1px solid var(--green); }
.chip-settled { background: var(--cobalt-soft); color: var(--cobalt);
                border: 1px solid var(--cobalt); }
.chip-refused, .chip-revoked { background: var(--red-orange-soft);
                color: var(--red-orange); border: 1px solid var(--red-orange); }
.chip-pending { background: var(--amber-soft); color: var(--amber);
                border: 1px solid var(--amber); }

/* Timeline */
.timeline { list-style: none; margin: 8px 0 20px; padding: 0; }
.timeline li {
  display: flex; gap: 14px; padding: 10px 4px;
  border-bottom: 1px dashed var(--line); align-items: flex-start;
}
.timeline li:last-child { border-bottom: none; }
.step-mark {
  flex: 0 0 118px; font-weight: 700; font-size: 13px;
  text-transform: uppercase; letter-spacing: 0.04em; padding-top: 2px;
}
.step-done .step-mark { color: var(--cobalt); }
.step-pending .step-mark { color: var(--muted); }
.step-dot {
  flex: 0 0 auto; width: 14px; height: 14px; border-radius: 50%;
  margin-top: 4px; border: 2px solid var(--muted); background: transparent;
}
.step-done .step-dot { background: var(--cobalt); border-color: var(--cobalt); }
.step-body { flex: 1; }
.step-state { font-size: 12px; font-weight: 700; letter-spacing: 0.05em; }
.step-done .step-state { color: var(--cobalt); }
.step-pending .step-state { color: var(--muted); }
.step-detail { font-size: 14px; margin-top: 2px; }
.step-when { font-size: 12px; color: var(--muted); }

/* Cards */
.card {
  background: var(--paper); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 18px; margin: 12px 0;
}
.card h4 { margin: 0 0 8px; font-size: 15px; }
.receipt-card { border-left: 5px solid #d4a900; background: var(--amber-soft); }
.receipt-card .mono { font-size: 14px; }
.seller-card h4 { margin-bottom: 4px; }
.standing-line { font-size: 13px; color: var(--muted); margin-bottom: 8px; }

/* Footer honesty block */
.honesty {
  margin-top: 48px; padding: 20px 22px; border: 2px solid var(--charcoal);
  border-radius: 10px; background: var(--paper);
}
.honesty h2 { margin-top: 0; border-bottom: none; padding-bottom: 0; }
.honesty ul { margin: 8px 0; padding-left: 20px; }
.honesty li { margin: 4px 0; }
.footer { margin-top: 24px; font-size: 12px; color: var(--muted);
           text-align: center; }
a { color: var(--cobalt); }
.note { font-size: 13px; color: var(--muted); }
.section-nav { font-size: 13px; margin: 12px 0; color: var(--muted); }
.section-nav a { margin-right: 14px; }
"""


def render_banner(snap):
    text = snap.get("banner") or (
        "LOCAL DEVELOPER PREVIEW \u2014 controlled agents, "
        "SIM_USD simulated funds, not a functioning market.")
    return (f'<div class="banner">{esc(text)}</div>')


def render_header(snap):
    meta = get_meta(snap)
    title = esc(meta.get("title") or "Agent Exchange Preview")
    gen = esc(meta.get("generated_at") or "unknown")
    home = esc(meta.get("home") or "")
    sim = meta.get("simulated", True)
    sim_line = ("All amounts in <strong>SIM_USD (simulated)</strong>. "
                "No real counterparties, no real money."
                if sim else "Simulation flag not set on this snapshot.")
    return f"""
<h1>{title}</h1>
<div class="meta">snapshot generated: <span class="mono">{gen}</span>
&middot; home: <code>{home}</code></div>
<p class="sim-note">{sim_line}</p>
"""


def render_buyer_view(snap):
    needs = get_list(snap, "needs")
    jobs = get_list(snap, "jobs")
    receipts = get_list(snap, "receipts")
    messages = get_list(snap, "messages")

    # --- needs ---
    if needs:
        rows = []
        for n in needs:
            rows.append(
                "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td>"
                "<td>{}</td><td>{}</td></tr>".format(
                    esc(n.get("need_id")), esc(n.get("buyer")),
                    esc(n.get("description")), esc(n.get("service")),
                    esc(money(n.get("max_price")))))
        needs_html = ("<table><tr><th>Need</th><th>Buyer</th><th>Description</th>"
                      "<th>Service</th><th>Max price</th></tr>"
                      + "".join(rows) + "</table>")
    else:
        needs_html = '<p class="note">No needs posted in this snapshot.</p>'

    # --- matched offers (OFFER_LIST / MATCH_RESPONSE messages) ---
    offer_rows = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        kind = str(m.get("kind", "")).upper()
        if kind in ("OFFER_LIST", "MATCH_REQUEST", "MATCH_RESPONSE"):
            body = m.get("body")
            body_txt = json.dumps(body, sort_keys=True) if body else ""
            offer_rows.append(
                "<tr><td class='mono'>{}</td><td>{}</td><td>{} &rarr; {}</td>"
                "<td class='mono'>{}</td></tr>".format(
                    esc(m.get("msg_id")), esc(kind),
                    esc(m.get("sender")), esc(m.get("recipient")),
                    esc(body_txt)))
    if offer_rows:
        offers_html = ("<table><tr><th>Message</th><th>Kind</th><th>Route</th>"
                       "<th>Body</th></tr>" + "".join(offer_rows) + "</table>")
    else:
        offers_html = ('<p class="note">No offer/match messages in this '
                       "snapshot.</p>")

    # --- commissions (jobs) ---
    if jobs:
        rows = []
        for j in jobs:
            chip_cls, chip_lbl = job_chip(j.get("status"), j.get("verdict"))
            rows.append(
                "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td>"
                "<td><span class='chip {}'>{}</span></td>"
                "<td>{}</td></tr>".format(
                    esc(j.get("job_id")), esc(j.get("seller")),
                    esc(money(j.get("amount"))), chip_cls, esc(chip_lbl),
                    esc(j.get("verdict"))))
        jobs_html = ("<table><tr><th>Job</th><th>Seller</th><th>Amount</th>"
                     "<th>Status</th><th>Verdict</th></tr>"
                     + "".join(rows) + "</table>")
    else:
        jobs_html = '<p class="note">No jobs in this snapshot.</p>'

    # --- verdicts ---
    verdict_rows = []
    for j in jobs:
        if j.get("verdict"):
            verdict_rows.append(
                "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td></tr>".format(
                    esc(j.get("job_id")), esc(j.get("verdict")),
                    esc(j.get("note") or j.get("settlement_id") or "")))
    if verdict_rows:
        verdicts_html = ("<table><tr><th>Job</th><th>Verdict</th>"
                         "<th>Note</th></tr>"
                         + "".join(verdict_rows) + "</table>")
    else:
        verdicts_html = '<p class="note">No verdicts recorded.</p>'

    # --- receipts (amber/gold) ---
    if receipts:
        cards = []
        for r in receipts:
            cards.append(
                "<div class='card receipt-card'><h4>Receipt for "
                "<span class='mono'>{}</span></h4>"
                "<div>settlement: <span class='mono'>{}</span></div>"
                "<div>amount: {}</div>"
                "<div>hash: <span class='mono'>{}</span></div></div>".format(
                    esc(r.get("job_id")), esc(r.get("settlement_id")),
                    esc(money(r.get("amount"))), esc(r.get("hash"))))
        receipts_html = "".join(cards)
    else:
        receipts_html = '<p class="note">No receipts issued in this snapshot.</p>'

    return f"""
<section id="buyer" class="view" aria-label="Buyer view">
<h2>Buyer view</h2>
<h3>Needs</h3>
{needs_html}
<h3>Matched offers</h3>
{offers_html}
<h3>Commissions</h3>
{jobs_html}
<h3>Verdicts</h3>
{verdicts_html}
<h3>Receipts</h3>
{receipts_html}
</section>
"""


def _seller_names(snap):
    """Seller names from agents (role contains 'seller'), plus any names
    appearing on listings, in listing order."""
    names = []
    for a in get_list(snap, "agents"):
        if isinstance(a, dict) and "seller" in str(a.get("role", "")).lower():
            if a.get("name") and a["name"] not in names:
                names.append(a["name"])
    for l in get_list(snap, "listings"):
        if isinstance(l, dict) and l.get("seller_name"):
            if l["seller_name"] not in names:
                names.append(l["seller_name"])
    return names


def render_seller_view(snap):
    listings = get_list(snap, "listings")
    jobs = get_list(snap, "jobs")
    receipts = get_list(snap, "receipts")
    cards = []
    for name in _seller_names(snap):
        own_listings = [l for l in listings
                        if isinstance(l, dict) and l.get("seller_name") == name]
        own_jobs = [j for j in jobs
                    if isinstance(j, dict) and j.get("seller") == name]
        own_job_ids = {j.get("job_id") for j in own_jobs}
        own_receipts = [r for r in receipts
                        if isinstance(r, dict) and r.get("job_id") in own_job_ids]

        if own_listings:
            lrows = []
            for l in own_listings:
                reason = l.get("revoked_reason")
                reason_txt = (f"<br><span class='note'>reason: "
                              f"{esc(reason)}</span>" if reason else "")
                lrows.append(
                    "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td><td>{}</td></tr>".format(
                        esc(l.get("listing_id")), esc(l.get("capability")),
                        esc(money(l.get("price"))),
                        standing_badge(l.get("standing")) + reason_txt,
                        esc(l.get("terms"))))
            listings_html = ("<table><tr><th>Listing</th><th>Capability</th>"
                             "<th>Price</th><th>Standing</th><th>Terms</th></tr>"
                             + "".join(lrows) + "</table>")
        else:
            listings_html = '<p class="note">No listings.</p>'

        if own_jobs:
            jrows = []
            for j in own_jobs:
                chip_cls, chip_lbl = job_chip(j.get("status"), j.get("verdict"))
                jrows.append(
                    "<tr><td class='mono'>{}</td><td>{}</td>"
                    "<td><span class='chip {}'>{}</span></td></tr>".format(
                        esc(j.get("job_id")), esc(money(j.get("amount"))),
                        chip_cls, esc(chip_lbl)))
            jobs_html = ("<table><tr><th>Job</th><th>Amount</th>"
                         "<th>Status</th></tr>" + "".join(jrows) + "</table>")
        else:
            jobs_html = '<p class="note">No jobs worked.</p>'

        if own_receipts:
            rrows = []
            for r in own_receipts:
                rrows.append(
                    "<tr><td class='mono'>{}</td><td class='mono'>{}</td>"
                    "<td>{}</td><td class='mono'>{}</td></tr>".format(
                        esc(r.get("job_id")), esc(r.get("settlement_id")),
                        esc(money(r.get("amount"))),
                        esc(short_hash(r.get("hash")))))
            receipts_html = ("<table><tr><th>Job</th><th>Settlement</th>"
                             "<th>Amount</th><th>Hash</th></tr>"
                             + "".join(rrows) + "</table>")
        else:
            receipts_html = '<p class="note">No settlements received.</p>'

        any_revoked = any(str(l.get("standing", "")).lower() == "revoked"
                          for l in own_listings)
        standing_line = ("<div class='standing-line'>standing: "
                         + ("<strong>has revoked listings</strong>"
                            if any_revoked else "all listings active")
                         + "</div>")

        cards.append(
            f"<div class='card seller-card'><h4>{esc(name)}</h4>"
            f"{standing_line}"
            f"<h3>Listings</h3>{listings_html}"
            f"<h3>Jobs worked</h3>{jobs_html}"
            f"<h3>Settlements received</h3>{receipts_html}</div>")

    if not cards:
        return ('<section id="seller" class="view" aria-label="Seller view">'
                '<h2>Seller view</h2>'
                '<p class="note">No sellers in this snapshot.</p></section>')
    return ('<section id="seller" class="view" aria-label="Seller view">'
            '<h2>Seller view</h2>' + "".join(cards) + "</section>")


def render_timeline(snap):
    items = []
    for key, label, done, t, actor, detail in timeline_steps(snap):
        state = "done" if done else "pending"
        state_lbl = "DONE" if done else "PENDING"
        when = (f"<div class='step-when'>t={esc(t)} &middot; {esc(actor)}</div>"
                if done and (t or actor) else "")
        detail_html = (f"<div class='step-detail'>{esc(detail)}</div>"
                       if done and detail else
                       "<div class='step-detail note'>not reached in this "
                       "snapshot</div>")
        items.append(
            f"<li class='step-{state}'>"
            f"<div class='step-mark'>{esc(label)}</div>"
            f"<div class='step-dot' aria-hidden='true'></div>"
            f"<div class='step-body'>"
            f"<div class='step-state'>{state_lbl}</div>"
            f"{when}{detail_html}"
            f"</div></li>")
    return ("<h2 id='timeline'>Exchange timeline</h2>"
            "<p class='note'>The kernel loop, in order. Each step is marked "
            "from the snapshot's own events and messages.</p>"
            "<ol class='timeline'>" + "".join(items) + "</ol>")


def render_listings(snap):
    listings = get_list(snap, "listings")
    if not listings:
        return ("<h2 id='listings'>Listings</h2>"
                "<p class='note'>No listings in this snapshot.</p>")
    rows = []
    for l in listings:
        reason = l.get("revoked_reason")
        reason_txt = (f"<br><span class='note'>reason: {esc(reason)}</span>"
                      if reason else "")
        rows.append(
            "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td>"
            "<td class='mono'>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td></tr>".format(
                esc(l.get("listing_id")), esc(l.get("seller_name")),
                esc(l.get("capability")), esc(l.get("artifact")),
                esc(money(l.get("price"))),
                standing_badge(l.get("standing")) + reason_txt,
                esc(l.get("terms"))))
    return ("<h2 id='listings'>Listings</h2>"
            "<table><tr><th>Listing</th><th>Seller</th><th>Capability</th>"
            "<th>Artifact</th><th>Price</th><th>Standing</th><th>Terms</th></tr>"
            + "".join(rows) + "</table>")


def render_transactions(snap):
    jobs = get_list(snap, "jobs")
    receipts = get_list(snap, "receipts")
    parts = ["<h2 id='transactions'>Transaction history</h2>"]
    if jobs:
        rows = []
        for j in jobs:
            chip_cls, chip_lbl = job_chip(j.get("status"), j.get("verdict"))
            rows.append(
                "<tr><td class='mono'>{}</td><td>{} &rarr; {}</td>"
                "<td>{}</td><td><span class='chip {}'>{}</span></td>"
                "<td>{}</td><td class='mono'>{}</td></tr>".format(
                    esc(j.get("job_id")), esc(j.get("buyer")),
                    esc(j.get("seller")), esc(money(j.get("amount"))),
                    chip_cls, esc(chip_lbl), esc(j.get("verdict")),
                    esc(j.get("settlement_id"))))
        parts.append("<h3>Jobs</h3><table><tr><th>Job</th><th>Route</th>"
                     "<th>Amount</th><th>Status</th><th>Verdict</th>"
                     "<th>Settlement</th></tr>" + "".join(rows) + "</table>")
    else:
        parts.append("<p class='note'>No jobs in this snapshot.</p>")
    if receipts:
        rows = []
        for r in receipts:
            rows.append(
                "<tr><td class='mono'>{}</td><td class='mono'>{}</td>"
                "<td>{}</td><td class='mono'>{}</td></tr>".format(
                    esc(r.get("job_id")), esc(r.get("settlement_id")),
                    esc(money(r.get("amount"))), esc(r.get("hash"))))
        parts.append("<h3>Receipts</h3><table><tr><th>Job</th>"
                     "<th>Settlement</th><th>Amount</th><th>Hash</th></tr>"
                     + "".join(rows) + "</table>")
    return "".join(parts)


def render_ledger(snap):
    ledger = get_ledger(snap)
    balances = ledger.get("balances")
    transfers = ledger.get("transfers")
    parts = ["<h2 id='ledger'>Ledger (simulated funds)</h2>"]
    if isinstance(balances, dict) and balances:
        rows = "".join(
            "<tr><td class='mono'>{}</td><td>{}</td></tr>".format(
                esc(k), esc(money(v)))
            for k, v in balances.items())
        parts.append("<h3>Balances</h3><table><tr><th>Principal</th>"
                     "<th>Balance</th></tr>" + rows + "</table>")
    if isinstance(transfers, list) and transfers:
        rows = []
        for t in transfers:
            if not isinstance(t, dict):
                continue
            rows.append(
                "<tr><td class='mono'>{}</td><td class='mono'>{}</td>"
                "<td>{}</td><td>{}</td></tr>".format(
                    esc(t.get("from")), esc(t.get("to")),
                    esc(money(t.get("amount"))), esc(t.get("memo"))))
        parts.append("<h3>Transfers</h3><table><tr><th>From</th><th>To</th>"
                     "<th>Amount</th><th>Memo</th></tr>"
                     + "".join(rows) + "</table>")
    if len(parts) == 1:
        parts.append("<p class='note'>No ledger data in this snapshot.</p>")
    return "".join(parts)


def render_events(snap):
    events = get_list(snap, "events")
    if not events:
        return ""
    items = "".join(
        "<li><strong class='mono'>{}</strong> &mdash; {}</li>".format(
            esc(e.get("kind")), esc(e.get("detail")))
        for e in events if isinstance(e, dict))
    return ("<h2 id='events'>Control events</h2>"
            "<p class='note'>Authority changes: swaps, revocations, "
            "mandate actions. Talk is cheap; these are the moments the "
            "boundary moved.</p>"
            "<ul>" + items + "</ul>")


def render_honesty():
    return """
<div class="honesty" id="honesty">
<h2>What this is not</h2>
<ul>
  <li>Not a functioning market.</li>
  <li>No real counterparties.</li>
  <li>No real demand.</li>
  <li>No real money (SIM_USD is simulated).</li>
  <li>No reputation, no liquidity.</li>
</ul>
<p><strong>Built:</strong> the exchange kernel &mdash; the control boundary.
The transport carries talk; the kernel decides whether talk becomes
authority, installation, work, or payment. Everything on this page ran
locally against file-backed mailboxes, a JSON registry, and a simulated
ledger.</p>
</div>
<div class="footer">
Generated by ui/dashboard.py from a local snapshot. Nothing on this page
touched a network.
</div>
"""


def render_page(snap):
    meta = get_meta(snap)
    title = esc(meta.get("title") or "Agent Exchange Preview")
    body = (
        render_banner(snap) + "\n"
        '<div class="wrap">\n'
        + render_header(snap)
        + '<div class="view-wrap">\n'
        '<nav class="tabs" aria-label="Views">\n'
        '  <a class="tab" href="#buyer">Buyer view</a>\n'
        '  <a class="tab" href="#seller">Seller view</a>\n'
        '</nav>\n'
        + render_seller_view(snap)
        + render_buyer_view(snap)
        + "</div>\n"
        '<div class="section-nav" aria-label="Sections">\n'
        "  Sections:\n"
        '  <a href="#timeline">Timeline</a>\n'
        '  <a href="#listings">Listings</a>\n'
        '  <a href="#transactions">Transactions</a>\n'
        '  <a href="#ledger">Ledger</a>\n'
        '  <a href="#events">Control events</a>\n'
        '  <a href="#honesty">What this is not</a>\n'
        "</div>\n"
        + render_timeline(snap)
        + render_listings(snap)
        + render_transactions(snap)
        + render_ledger(snap)
        + render_events(snap)
        + render_honesty()
        + "</div>\n"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + title + "</title>\n"
        "<style>" + CSS + "</style>\n"
        "</head>\n<body>\n" + body + "</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render the agent-exchange developer preview UI "
                    "from a snapshot JSON.")
    parser.add_argument("snapshot", nargs="?",
                        help="snapshot JSON file")
    parser.add_argument("-o", "--output", default=None,
                        help="output HTML path (default: ui/index.html "
                             "next to this script)")
    parser.add_argument("--demo", action="store_true",
                        help="use an inline sample snapshot instead of a file")
    args = parser.parse_args(argv)

    if args.demo:
        snap = demo_snapshot()
    elif args.snapshot:
        snap = load_snapshot(args.snapshot)
    else:
        parser.error("provide a snapshot file or --demo")

    out_path = Path(args.output) if args.output else \
        Path(__file__).resolve().parent / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    page = render_page(snap)
    out_path.write_text(page, encoding="utf-8")

    if args.demo:
        demo_path = out_path.parent / "demo-snapshot.json"
        demo_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        print(f"demo snapshot: {demo_path}", file=sys.stderr)

    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
