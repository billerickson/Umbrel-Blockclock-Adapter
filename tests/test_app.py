import os
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from blockclock_adapter.app import (
    Adapter,
    BlockclockClient,
    Config,
    block_age_minutes,
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

    def test_calculates_block_age_in_whole_minutes(self):
        self.assertEqual(block_age_minutes([{"timestamp": 1_000}], now=1_121), 2)
        self.assertEqual(block_age_minutes([{"timestamp": 1_200}], now=1_121), 0)

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

    def test_block_age_shows_minutes(self):
        path, query = BlockclockClient._display_request("block_age", 17)
        self.assertEqual(path, "/api/show/number/17")
        self.assertEqual(query["pair"], "BLK/AGE")
        self.assertEqual(query["br"], "MINUTES")

    def test_blocks_found_shows_unit_on_left(self):
        path, query = BlockclockClient._display_request("blocks_found", 3)
        self.assertEqual(path, "/api/show/number/3")
        self.assertEqual(query["pair"], "BLOCKS FOUND")

    def test_hashrate_uses_text_endpoint(self):
        path, query = BlockclockClient._display_request("hash_rate", 813_234_574_831.3806)
        self.assertEqual(path, "/api/show/text/813GH")
        self.assertEqual(query["tl"], "POOL HASH")

    def test_status_includes_deployed_commit(self):
        with patch.dict(
            os.environ, {"BLOCKCLOCK_ADAPTER_VERSION": "0123456789abcdef"}
        ):
            status = Adapter(self.config).status()
        self.assertEqual(status["deployed_commit"], "0123456789abcdef")

    def test_flash_lights_uses_flash_endpoint(self):
        client = BlockclockClient(self.config)
        with patch.object(client, "_get") as get:
            client.flash_lights()
        get.assert_called_once_with("/api/lights/flash")


class AdapterTests(unittest.TestCase):
    class FixedCollector:
        def __init__(self, values):
            self.values = values

        def collect(self):
            return dict(self.values), {}

    class RecordingBlockclock:
        def __init__(self):
            self.shows = []
            self.flashes = 0

        def show(self, metric, value):
            self.shows.append((metric, value))

        def flash_lights(self):
            self.flashes += 1

    def setUp(self):
        with patch.dict(os.environ, {}, clear=True):
            self.config = Config()

    def test_blocks_found_stays_in_rotation_until_acknowledged(self):
        with TemporaryDirectory() as temporary_directory:
            config = replace(
                self.config,
                enabled_metrics=("block_height", "blocks_found"),
                state_file=f"{temporary_directory}/state.json",
            )
            adapter = Adapter(config)
            blockclock = self.RecordingBlockclock()
            adapter.blockclock = blockclock

            adapter.collector = self.FixedCollector(
                {"block_height": 100, "blocks_found": 0}
            )
            adapter.run_once()
            self.assertEqual(blockclock.shows[-1], ("block_height", 100))
            self.assertEqual(blockclock.flashes, 0)

            adapter.collector = self.FixedCollector(
                {"block_height": 101, "blocks_found": 1}
            )
            adapter.run_once()
            self.assertEqual(blockclock.shows[-1], ("blocks_found", 1))
            self.assertEqual(blockclock.flashes, 1)
            self.assertTrue(adapter.status()["blocks_found_alert_active"])

            adapter.run_once()
            adapter.run_once()
            self.assertEqual(
                blockclock.shows[-2:],
                [("block_height", 101), ("blocks_found", 1)],
            )
            self.assertEqual(blockclock.flashes, 1)

            acknowledged = adapter.acknowledge_block_found()
            self.assertEqual(acknowledged["current_block_counter"], 1)
            self.assertFalse(acknowledged["blocks_found_alert_active"])

            adapter.run_once()
            self.assertEqual(blockclock.shows[-1], ("block_height", 101))
            self.assertEqual(blockclock.flashes, 1)

            restarted = Adapter(config)
            self.assertEqual(restarted.status()["current_block_counter"], 1)
            self.assertEqual(restarted.status()["last_flashed_blocks_found"], 1)


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

    def test_rejects_only_conditional_metric(self):
        with patch.dict(os.environ, {"ENABLED_METRICS": "blocks_found"}, clear=True):
            config = Config()
            with self.assertRaisesRegex(ValueError, "rotating metric"):
                config.validate()


if __name__ == "__main__":
    unittest.main()
