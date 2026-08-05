import os
import unittest
from unittest.mock import patch

from blockclock_adapter.app import (
    BlockclockClient,
    Config,
    blocks_found_count,
    compact_hashrate,
    parse_height,
    parse_price,
)


class ParsingTests(unittest.TestCase):
    def test_parses_plain_and_object_heights(self):
        self.assertEqual(parse_height("961174"), 961174)
        self.assertEqual(parse_height({"blockHeight": 961174}), 961174)

    def test_parses_coinbase_price(self):
        self.assertEqual(parse_price({"data": {"amount": "115000.25"}}), 115000.25)

    def test_counts_pool_blocks(self):
        self.assertEqual(blocks_found_count([]), 0)
        self.assertEqual(blocks_found_count([{"height": 1}, {"height": 2}]), 2)
        self.assertEqual(blocks_found_count(3), 3)

    def test_compacts_hashrate_to_seven_characters(self):
        self.assertEqual(compact_hashrate(813_234_574_831.3806), "813GH")
        self.assertLessEqual(len(compact_hashrate(1_234_567)), 7)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True):
            self.config = Config()

    def test_moscow_time_uses_numeric_endpoint(self):
        path, query = BlockclockClient._display_request("moscow_time", 870)
        self.assertEqual(path, "/api/show/number/870")
        self.assertEqual(query["pair"], "SAT/USD")

    def test_fastest_fee_shows_unit_on_left(self):
        path, query = BlockclockClient._display_request("fastest_fee", 12)
        self.assertEqual(path, "/api/show/number/12")
        self.assertEqual(query["pair"], "SATS/VB")

    def test_hashrate_uses_text_endpoint(self):
        path, query = BlockclockClient._display_request("hash_rate", 813_234_574_831.3806)
        self.assertEqual(path, "/api/show/text/813GH")
        self.assertEqual(query["tl"], "POOL HASH")


class ConfigTests(unittest.TestCase):
    def test_rejects_unapproved_price_host(self):
        with patch.dict(
            os.environ,
            {
                "PRICE_API_URL": "https://example.com/price",
                "PRICE_ALLOWED_HOSTS": "api.coinbase.com",
            },
            clear=True,
        ):
            config = Config()
            with self.assertRaisesRegex(ValueError, "not in PRICE_ALLOWED_HOSTS"):
                config.validate()


if __name__ == "__main__":
    unittest.main()
