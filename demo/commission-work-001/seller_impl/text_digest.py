#!/usr/bin/env python3
"""text_digest — the seller's service implementation.

This file lives in the SELLER's directory. It is never copied into buyer
state: the buyer buys results, not code. Deterministic, stdlib only.
Input: a text file whose first line is a JSON header {"nonce": ...}.
Output: a JSON digest report on stdout.
"""
import hashlib
import json
import sys


def main() -> int:
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\n")
    header = json.loads(raw[:header_end].decode("utf-8"))
    body = raw[header_end + 1:]
    text = body.decode("utf-8")
    words = text.split()
    lines = text.split("\n")
    report = {
        "service": "text_digest",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "word_count": len(words),
        "line_count": len(lines),
        "nonce": header["nonce"],
    }
    sys.stdout.write(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
