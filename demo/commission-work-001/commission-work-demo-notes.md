# commission-work-demo notes

Cinematic product demo for the commission-work-001 local preview. For
review, not published.

## Source

`demo-commission.html` — deterministic, time-driven. Every shown outcome
(job ids, agreement digest, input hash, receipt filename, the 7 acceptance
checks, the refusal codes) comes from a real quickstart run
(`bash demo/commission-work-001/quickstart.sh`). Labels
"DEMO VISUALIZATION" and "LOCAL DEVELOPER PREVIEW" are baked into every
frame. Footer on every frame: SIMULATED FUNDS · NO REAL PAYMENTS.

## Build

- Frames: 1056, 24 fps, 1080x1920, captured headless via Chromium CDP
  (`window.renderAt(t)` then `Page.captureScreenshot` per frame), zero paid
  calls. Captured in two 22 s segments and concatenated.
- Audio: original, synthesized with numpy (`gen_audio.py`). Quiet pulse bed,
  scene-change ticks, confirmation chimes at VERIFY/SETTLE, dull thuds at
  refusals. Peaks at -3 dBFS. Works muted.
- Mux: `ffmpeg -c:v copy -c:a aac -movflags +faststart`.
- Result: 44.0 s, 1080x1920, mp4.

## Scenes

setup → agreement → work → verify (7 of 7) → settle (exactly once, replay
refused) → reject (no payment) → agent boundaries (4 refusals) → revoke
(new work blocked, earned obligation stands) → end card.

## Limits

One service (text_digest), local only, simulated funds. The seller keeps its
implementation; the buyer gets a result plus a receipt; nothing installs.
Separate local identities demonstrate an authority boundary, not a market,
not external adoption. End card states the quickstart command and the
limits: no discovery, no market, no real payments.

## Hashes

- video sha256: c57b6db9d400df591541ebb0f5ca8789ee99e788ad3633c4e907cb836ee24fa9
- poster: commission-work-demo-poster.png (25 s frame, SETTLE scene)
