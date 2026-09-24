# commission-work-demo notes

Cinematic product demo for the commission-work-001 local preview. For
review, not published.

## Source

`demo-commission.html` — deterministic, time-driven. Every shown outcome
(job id `job-7397c2a1ac4b`, agreement digest `471abb562b7d394d…`, input hash
`c8f93b5c028eb61e…`, receipt `settlement_job-7397c2a1ac4b.json`, the 7
acceptance checks, the idempotency key `settle:471abb562b7d394d…`, the
refusal codes) comes from a real run of the fixed commission module
(`commission.py` on this branch, home /tmp/cw-rec). Labels
"DEMO VISUALIZATION" and "LOCAL DEVELOPER PREVIEW" are baked into every
frame. Footer on every frame: SIMULATED FUNDS · NO REAL PAYMENTS.

## Build

- Frames: 1248, 24 fps, 1080x1920, captured headless via Chromium CDP
  (`window.renderAt(t)` then `Page.captureScreenshot` per frame), zero paid
  calls. Captured in two 26 s segments and concatenated.
- Audio: original, synthesized with numpy (`gen_audio.py`). Quiet pulse bed,
  scene-change ticks, confirmation chimes at VERIFY/SETTLE, dull thuds at
  refusals. Peaks at -3 dBFS. Works muted.
- Mux: `ffmpeg -c:v copy -c:a aac -movflags +faststart`.
- Result: 52.0 s, 1080x1920, mp4.

## Scenes

setup → agreement → work → verify (7 of 7) → settle (exactly once,
idempotency key, replay refused) → reject (no payment) → agent boundaries
(4 refusals, trusted-operator note) → revoke (new work blocked, earned
obligation stands) → hardening (three fixes, three refusals) → end card.

## Limits

One service (text_digest), local only, simulated funds. The seller keeps its
implementation; the buyer gets a result plus a receipt; nothing installs.
Separate local identities demonstrate an authority boundary, not a market,
not external adoption. The BOUNDARIES and END scenes state the honest
framing: trusted-operator simulation — the operator holds every key on this
host; the preview demonstrates and tests the rules, not custodial
separation. End card states the quickstart command and the limits: no
market, no real payments.

## Hashes

- video sha256: 844e8ec5b090980e949de74fb1771ba1721ba9508c01bb636e2ce4d9c1259d29
- poster: commission-work-demo-poster.png (25 s frame, SETTLE scene)
