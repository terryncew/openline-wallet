# Agent Exchange Preview — interface contracts (frozen for this build)

Everything is local, deterministic, simulated. Currency is SIM_USD (simulated)
everywhere. Controlled agents only. This is the exchange KERNEL; the bazaar
(real counterparties, demand, money, reputation, liquidity) is deliberately
not built.

## Package layout

```
demo/agent-exchange-001/
  exchange/
    interfaces.py    # Transport, Registry, Matcher, Receiver, Settlement (ABCs)
    transport.py     # LocalMailboxTransport (file-backed JSONL mailboxes)
    registry.py      # FileRegistry (JSON file)
    matcher.py       # KeywordMatcher (reference matcher)
    kernel.py        # Exchange orchestrator: drives the loop on commission.py
  ui/
    dashboard.py     # generates ui/index.html from a run snapshot
    index.html       # generated (never hand-edited)
  tests/
    test_transport.py
    test_registry.py
    test_kernel.py
  quickstart.sh      # full loop end to end
  README.md
  CLAIMS.md
```

## Home state

One exchange home directory: `<home>/` contains:
- the commission home (commission.py machinery) at `<home>/commission/`
- mailboxes at `<home>/mail/<recipient>.jsonl` (append-only)
- the registry at `<home>/registry.json`
- run snapshots at `<home>/snapshots/*.json`

The kernel passes `home=<home>/commission` to commission.py functions.

## Message envelope (transport)

```python
{
  "msg_id": "msg-<sha16>",
  "sender": "<agent name>",
  "recipient": "<agent name>",
  "kind": "NEED_POSTED | OFFER_LIST | MATCH_REQUEST | MATCH_RESPONSE | "
          "AGREEMENT_PROPOSED | AGREEMENT_FROZEN | RESULT_DELIVERED | "
          "VERDICT | SETTLED | RECEIPT | REVOKED",
  "body": { ... },   # kind-specific, plain JSON
  "at": "<utc iso>",
}
```

`msg_id` is derived deterministically from (sender, recipient, kind,
canonical body, sequence number). No network. No encryption claims.

## Transport interface

```python
class Transport(ABC):
    @abstractmethod
    def send(self, sender: str, recipient: str, kind: str, body: dict) -> str:
        """Append one message; return msg_id."""
    @abstractmethod
    def receive(self, recipient: str) -> list[dict]:
        """Return and clear all pending messages for recipient."""
    @abstractmethod
    def peek(self, recipient: str) -> list[dict]:
        """Return pending messages without clearing."""

class LocalMailboxTransport(Transport):
    def __init__(self, maildir: Path): ...
```

The seam: an AX/MCP/A2A adapter would implement `Transport` against real
agent messaging. OpenLine's position: the transport carries talk; the
kernel decides whether talk becomes authority, installation, work, or
payment.

## Registry interface

Listing record:
```python
{
  "listing_id": "listing-<sha16>",
  "seller_id": "<principal>",
  "seller_name": "seller-a",
  "capability": "digest-report",
  "service": "text_digest",
  "artifact": "text_digest@1.0",
  "price": 50,
  "currency": "SIM_USD (simulated)",
  "terms": "results only; implementation retained by seller",
  "required_permissions": [],
  "acceptance": { ... },          # acceptance criteria offered
  "evidence": ["<receipt hash>", ...],  # prior acceptance evidence, may be []
  "standing": "active | revoked",
  "revoked_reason": null | "<reason>",
  "created_at": "<utc iso>",
}
```

```python
class Registry(ABC):
    @abstractmethod
    def register(self, listing: dict) -> str: ...
    @abstractmethod
    def search(self, query: dict) -> list[dict]: ...
    @abstractmethod
    def get(self, listing_id: str) -> dict | None: ...
    @abstractmethod
    def revoke(self, listing_id: str, reason: str) -> None: ...
    @abstractmethod
    def all(self) -> list[dict]: ...

class FileRegistry(Registry):
    def __init__(self, path: Path): ...
```

## Matcher interface

```python
class Matcher(ABC):
    @abstractmethod
    def rank(self, need: dict, candidates: list[dict]) -> list[tuple[dict, float, list[str]]]:
        """Return (listing, score, reasons) sorted best-first. No scores are
        claims of quality; they are the matcher's stated ranking only."""

class KeywordMatcher(Matcher):
    """Reference: filters revoked + over-budget, scores keyword overlap."""
```

Need record:
```python
{"need_id": "need-<sha16>", "buyer": "<agent name>", "description": "...",
 "keywords": [...], "max_price": 50, "service": "text_digest | null",
 "created_at": "<utc iso>"}
```

## Receiver / Settlement interfaces

```python
class Receiver(ABC):
    @abstractmethod
    def check(self, job_id: str) -> dict: ...
        # -> {"verdict": "accepted|rejected", "checks": [...], ...}

class Settlement(ABC):
    @abstractmethod
    def settle(self, job_id: str) -> dict: ...
        # -> {"settlement_id": ..., "amount": ..., "status": "settled|already"}
```

Reference implementations (`CommissionReceiver`, `CommissionSettlement` in
kernel.py) delegate to commission.py's cmd_verify / cmd_settle, inheriting
its invariants: idempotent verify, exactly-once settlement, truthful
PENDING reporting, read-only reconcile.

## Kernel loop (kernel.py)

`Exchange(home, transport, registry, matcher)` with methods:

- `bootstrap()` — commission init, keys for owner/agent/sellers, fund owner,
  register 3 sellers' listings, delegate buyer-agent budget.
- `post_need(description, keywords, max_price, service=None)` -> need
- `advertise()` -> sends OFFER_LIST via transport (registry already has them)
- `find(need)` -> MATCH_RESPONSE candidates ranked
- `select(need_id, listing_id)` -> returns chosen listing
- `commission(need, listing)` -> runs the commission loop:
  owner delegates (if needed) -> transport AGREEMENT_PROPOSED ->
  commission.cmd_commission (freezes agreement, reserves) ->
  seller cmd_work + cmd_submit -> transport RESULT_DELIVERED ->
  receiver.check (verify) -> VERDICT message ->
  settlement.settle on accept (exactly once) -> RECEIPT message ->
  snapshot written.
- `swap_buyer_agent()` — owner revokes old agent's mandate, delegates a
  NEW agent; the new agent has no allowance and cannot touch old jobs'
  funds; old agent's actions blocked (MANDATE_REVOKED).
- `revoke_seller(listing_id, reason)` — registry standing=revoked;
  future commissions against that listing refused; existing receipts
  survive.
- `snapshot(name)` — writes `<home>/snapshots/<name>.json`: needs, listings,
  messages, jobs summary, allowances summary, ledger summary, receipts.

The kernel imports commission.py as a module (`sys.path` insert of the
commission-work-001 dir) and calls its `cmd_*` functions with simple
Namespace objects. It never touches commission's durable files directly
except through those functions and the documented read helpers
(_read_json equivalents local to the kernel).

## Test requirements

- transport: send/receive/peek round-trip; messages are ordered; ids stable.
- registry: register/search/get/revoke; revoked listings excluded from search.
- kernel: full loop settles exactly once; interrupt mid-commission
  (COMMISSION_CRASH_AFTER=commission-txn) then retry converges to one job /
  one reservation; interrupt between verdict and release
  (COMMISSION_CRASH_AFTER=verify-verdict) then reconcile reports PENDING
  and a later verify completes the release once; swap agent does not leak
  authority; revoked listing refuses new commission; receipts survive.
- Everything runs under `python -m unittest discover -s tests` with
  PYTHONPATH including src and the demo dirs. No network. $0 spend.

## UI contract

`ui/dashboard.py` reads one snapshot JSON and writes `ui/index.html`:
views for buyer and seller; timeline of the loop; listings/needs/offers/
receipts/transaction history; statuses ACCEPTED / REFUSED / REVOKED.
Every page carries a persistent banner: "LOCAL DEVELOPER PREVIEW —
controlled agents, SIM_USD simulated funds, not a functioning market."
No forms that claim real commerce. Styling: cream/charcoal, cobalt accent,
warm red-orange for refused/revoked, amber/gold for receipts.
