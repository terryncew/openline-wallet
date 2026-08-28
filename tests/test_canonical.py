from __future__ import annotations

import unittest

from openline_wallet.canonical import canonical_json, strict_json_loads
from openline_wallet.errors import WalletError


class CanonicalJsonTests(unittest.TestCase):
    def test_keys_are_sorted_and_integers_are_stable(self) -> None:
        self.assertEqual(canonical_json({"z": 2, "a": 1}), b'{"a":1,"z":2}')

    def test_duplicate_keys_fail_closed(self) -> None:
        with self.assertRaisesRegex(WalletError, "DUPLICATE_JSON_KEY"):
            strict_json_loads('{"a":1,"a":2}')

    def test_floats_fail_closed(self) -> None:
        with self.assertRaisesRegex(WalletError, "FLOAT_FORBIDDEN"):
            canonical_json({"risk": 0.5})

    def test_unsafe_integer_fails_closed(self) -> None:
        with self.assertRaisesRegex(WalletError, "INTEGER_OUT_OF_RANGE"):
            canonical_json({"value": 1 << 60})


if __name__ == "__main__":
    unittest.main()
