# Signed Webhook Feature Contract

This contract is part of the frozen Spec Kit feature artifacts for `001-signed-webhook`.

## Producer Input

The producer receives:

- `event`: non-empty string
- `resource_id`: non-empty string
- `amount_cents`: nonnegative integer, excluding booleans
- `timestamp`: integer Unix seconds, excluding booleans
- `nonce`: ASCII string of length 16 through 64
- `secret`: bytes used only by the local fixture

## Canonical Payload

The signed payload contains exactly these keys:

```text
amount_cents
event
nonce
resource_id
timestamp
```

Serialization is UTF-8 JSON with `sort_keys=True`, separators `(',', ':')`, `ensure_ascii=False`, and no insignificant whitespace. The producer MUST preserve the exact caller-supplied nonce.

## Signature

Algorithm: HMAC-SHA256 over the exact canonical payload bytes.

Header name: `X-OpenLine-Signature`

Header value:

```text
v1=<64 lowercase hexadecimal characters>
```

## Receiver Results

- Accepted unseen valid request: `200`, `{"status": "accepted"}`
- Malformed or non-canonical payload: `400`, `{"error": "bad_payload"}`
- Invalid signature: `401`, `{"error": "invalid_signature"}`
- Replay of a nonce from a previously accepted request: `409`, `{"error": "replay"}`
- Timestamp with absolute skew greater than 300 seconds: `422`, `{"error": "stale_timestamp"}`

Replay state is local to one receiver instance in this fixture.
