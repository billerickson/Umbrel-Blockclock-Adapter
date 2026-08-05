from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger("blockclock-adapter")

METRIC_ORDER = (
    "block_height",
    "block_age",
    "fastest_fee",
    "btc_price",
    "moscow_time",
    "hash_rate",
    "blocks_found",
)


def env_int(name: str, default: int, minimum: int) -> int:
    value = int(os.environ.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    mempool_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "MEMPOOL_BASE_URL", "http://127.0.0.1:3006"
        )
    )
    pool_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "POOL_API_URL", "http://127.0.0.1:2019/api/pool"
        )
    )
    price_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "PRICE_API_URL", "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        )
    )
    price_allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: parse_csv(
            os.environ.get("PRICE_ALLOWED_HOSTS", "api.coinbase.com")
        )
    )
    blockclock_url: str = field(
        default_factory=lambda: os.environ.get(
            "BLOCKCLOCK_URL", "http://192.168.40.20"
        )
    )
    blockclock_password: str = field(
        default_factory=lambda: os.environ.get("BLOCKCLOCK_PASSWORD", "")
    )
    enabled_metrics: tuple[str, ...] = field(
        default_factory=lambda: parse_csv(
            os.environ.get("ENABLED_METRICS", ",".join(METRIC_ORDER))
        )
    )
    display_interval_seconds: int = field(
        default_factory=lambda: env_int("DISPLAY_INTERVAL_SECONDS", 300, 60)
    )
    source_timeout_seconds: int = field(
        default_factory=lambda: env_int("SOURCE_TIMEOUT_SECONDS", 10, 1)
    )
    bind_host: str = field(
        default_factory=lambda: os.environ.get("BIND_HOST", "127.0.0.1")
    )
    bind_port: int = field(default_factory=lambda: env_int("BIND_PORT", 21022, 1))
    state_file: str = field(
        default_factory=lambda: os.environ.get(
            "STATE_FILE", "/var/lib/blockclock-adapter/state.json"
        )
    )

    def validate(self) -> None:
        unknown = set(self.enabled_metrics) - set(METRIC_ORDER)
        if unknown:
            raise ValueError(f"unknown ENABLED_METRICS: {', '.join(sorted(unknown))}")
        if not self.enabled_metrics:
            raise ValueError("ENABLED_METRICS cannot be empty")
        if set(self.enabled_metrics) == {"blocks_found"}:
            raise ValueError("ENABLED_METRICS must include a rotating metric")

        self._require_url(self.mempool_base_url, {"http"}, "MEMPOOL_BASE_URL")
        self._require_url(self.pool_api_url, {"http"}, "POOL_API_URL")
        price = self._require_url(self.price_api_url, {"https"}, "PRICE_API_URL")
        if price.hostname not in self.price_allowed_hosts:
            raise ValueError("PRICE_API_URL host is not in PRICE_ALLOWED_HOSTS")
        self._require_url(self.blockclock_url, {"http"}, "BLOCKCLOCK_URL")

    @staticmethod
    def _require_url(value: str, schemes: set[str], name: str) -> urllib.parse.ParseResult:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in schemes or not parsed.hostname:
            raise ValueError(f"invalid or unsafe {name}")
        return parsed


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        target = urllib.parse.urlparse(newurl)
        if target.scheme != "https" or target.hostname not in self.allowed_hosts:
            raise urllib.error.HTTPError(
                newurl, code, "redirect target is not allowlisted", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_json(url: str, timeout: int, opener: urllib.request.OpenerDirector | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "blockclock-umbrel-adapter/1.0"},
    )
    active_opener = opener or urllib.request.build_opener()
    with active_opener.open(request, timeout=timeout) as response:
        return json.load(response)


def open_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "blockclock-umbrel-adapter/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(128).decode("utf8").strip()


def parse_height(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, dict):
        for key in ("height", "blockHeight", "block_height", "tipHeight"):
            if key in value:
                return parse_height(value[key])
    raise ValueError("block height response has no recognized height")


def parse_price(value: Any) -> float:
    try:
        price = float(value["data"]["amount"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Coinbase response has no numeric data.amount") from error
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Coinbase returned an invalid BTC price")
    return price


def blocks_found_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int) and value >= 0:
        return value
    raise ValueError("blocksFound must be an array or non-negative integer")


def block_age_minutes(value: Any, now: float | None = None) -> int:
    try:
        timestamp = float(value[0]["timestamp"])
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("recent blocks response has no numeric timestamp") from error
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("recent block timestamp is invalid")
    current_time = time.time() if now is None else now
    return max(0, int((current_time - timestamp) // 60))


def compact_hashrate(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise ValueError("totalHashRate must be a non-negative number")
    units = ("H", "kH", "MH", "GH", "TH", "PH", "EH")
    scaled = value
    unit = units[0]
    for candidate in units[1:]:
        if scaled < 1000:
            break
        scaled /= 1000
        unit = candidate
    decimals = 0 if scaled >= 100 else 1 if scaled >= 10 else 2
    text = f"{scaled:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"[:7]


@dataclass
class Snapshot:
    values: dict[str, float | int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    refreshed_at: str | None = None
    last_displayed_metric: str | None = None
    last_displayed_at: str | None = None
    display_error: str | None = None


@dataclass
class AlertState:
    acknowledged_blocks_found: int = 0
    last_flashed_blocks_found: int = 0


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> AlertState:
        try:
            with self.path.open(encoding="utf8") as state_file:
                payload = json.load(state_file)
        except FileNotFoundError:
            return AlertState()
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read state file: {error}") from error

        acknowledged = self._non_negative_int(
            payload, "acknowledged_blocks_found"
        )
        last_flashed = self._non_negative_int(
            payload, "last_flashed_blocks_found"
        )
        return AlertState(acknowledged, last_flashed)

    def save(self, state: AlertState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = asdict(state)
        try:
            with temporary.open("w", encoding="utf8") as state_file:
                json.dump(payload, state_file, sort_keys=True)
                state_file.write("\n")
            os.replace(temporary, self.path)
        except OSError as error:
            raise ValueError(f"could not write state file: {error}") from error

    @staticmethod
    def _non_negative_int(payload: Any, key: str) -> int:
        try:
            value = payload[key]
        except (KeyError, TypeError) as error:
            raise ValueError(f"state file has no {key}") from error
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"state file has invalid {key}")
        return value


class DataCollector:
    def __init__(self, config: Config):
        self.config = config
        self.price_opener = urllib.request.build_opener(
            SafeRedirectHandler(set(config.price_allowed_hosts))
        )

    def collect(self) -> tuple[dict[str, float | int], dict[str, str]]:
        values: dict[str, float | int] = {}
        errors: dict[str, str] = {}

        self._capture("block_height", values, errors, self._block_height)
        self._capture("block_age", values, errors, self._block_age)
        self._capture("fastest_fee", values, errors, self._fastest_fee)
        self._capture("btc_price", values, errors, self._btc_price)
        self._capture("pool", values, errors, self._pool)

        if "btc_price" in values:
            values["moscow_time"] = round(100_000_000 / float(values["btc_price"]))

        return values, errors

    @staticmethod
    def _capture(
        name: str,
        values: dict[str, float | int],
        errors: dict[str, str],
        operation: Callable[[], dict[str, float | int]],
    ) -> None:
        try:
            values.update(operation())
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
            errors[name] = str(error)

    def _block_height(self) -> dict[str, int]:
        base = self.config.mempool_base_url.rstrip("/")
        canonical_url = f"{base}/api/blocks/tip/height"
        try:
            return {"block_height": parse_height(open_text(canonical_url, self.config.source_timeout_seconds))}
        except (OSError, ValueError, urllib.error.URLError):
            fallback = open_json(f"{base}/api/v1", self.config.source_timeout_seconds)
            return {"block_height": parse_height(fallback)}

    def _fastest_fee(self) -> dict[str, int]:
        base = self.config.mempool_base_url.rstrip("/")
        response = open_json(
            f"{base}/api/v1/fees/recommended", self.config.source_timeout_seconds
        )
        value = int(response["fastestFee"])
        if value < 0:
            raise ValueError("fastestFee cannot be negative")
        return {"fastest_fee": value}

    def _block_age(self) -> dict[str, int]:
        base = self.config.mempool_base_url.rstrip("/")
        response = open_json(
            f"{base}/api/v1/blocks", self.config.source_timeout_seconds
        )
        return {"block_age": block_age_minutes(response)}

    def _btc_price(self) -> dict[str, float]:
        response = open_json(
            self.config.price_api_url,
            self.config.source_timeout_seconds,
            opener=self.price_opener,
        )
        return {"btc_price": parse_price(response)}

    def _pool(self) -> dict[str, float | int]:
        response = open_json(self.config.pool_api_url, self.config.source_timeout_seconds)
        return {
            "hash_rate": float(response["totalHashRate"]),
            "blocks_found": blocks_found_count(response["blocksFound"]),
        }


class BlockclockClient:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.blockclock_url.rstrip("/")
        handlers: list[Any] = []
        if config.blockclock_password:
            password_manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            password_manager.add_password(
                None, self.base_url, "blockclock", config.blockclock_password
            )
            handlers.append(urllib.request.HTTPDigestAuthHandler(password_manager))
        self.opener = urllib.request.build_opener(*handlers)

    def pause_backend_updates(self) -> None:
        self._get("/api/action/pause")

    def flash_lights(self) -> None:
        self._get("/api/lights/flash")

    def show(self, metric: str, value: float | int) -> None:
        path, query = self._display_request(metric, value)
        self._get(f"{path}?{urllib.parse.urlencode(query)}")

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "application/json", "User-Agent": "blockclock-umbrel-adapter/1.0"},
        )
        with self.opener.open(request, timeout=self.config.source_timeout_seconds) as response:
            return json.load(response)

    @staticmethod
    def _display_request(metric: str, value: float | int) -> tuple[str, dict[str, str]]:
        if metric == "block_height":
            return f"/api/show/number/{int(value)}", {"tl": "BLOCK HEIGHT", "br": "LOCAL NODE"}
        if metric == "block_age":
            return f"/api/show/number/{int(value)}", {
                "tl": "BLOCK AGE",
                "br": "MINUTES",
                "pair": "BLK/AGE",
            }
        if metric == "fastest_fee":
            return f"/api/show/number/{int(value)}", {
                "tl": "FASTEST FEE",
                "br": "sat/vB",
                "pair": "SATS/VB",
            }
        if metric == "btc_price":
            return f"/api/show/number/{round(float(value))}", {
                "tl": "BTC PRICE",
                "br": "COINBASE",
                "pair": "BTC/USD",
                "sym": "USD",
            }
        if metric == "moscow_time":
            return f"/api/show/number/{int(value)}", {
                "tl": "MOSCOW TIME",
                "br": "sats/USD",
                "pair": "SAT/USD",
            }
        if metric == "hash_rate":
            text = urllib.parse.quote(compact_hashrate(float(value)), safe="")
            return f"/api/show/text/{text}", {"tl": "POOL HASH", "br": "hash/s"}
        if metric == "blocks_found":
            return f"/api/show/number/{int(value)}", {
                "tl": "BLOCKS FOUND",
                "br": "POOL",
                "pair": "BLOCKS FOUND",
            }
        raise ValueError(f"unsupported metric: {metric}")


class Adapter:
    def __init__(self, config: Config):
        self.config = config
        self.deployed_commit = os.environ.get("BLOCKCLOCK_ADAPTER_VERSION", "unknown")
        self.collector = DataCollector(config)
        self.blockclock = BlockclockClient(config)
        self.state_store = StateStore(config.state_file)
        self.alert_state = self.state_store.load()
        self.snapshot = Snapshot()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.metric_index = 0

    def status(self) -> dict[str, Any]:
        with self.lock:
            result = asdict(self.snapshot)
            result["current_block_counter"] = (
                self.alert_state.acknowledged_blocks_found
            )
            result["last_flashed_blocks_found"] = (
                self.alert_state.last_flashed_blocks_found
            )
            result["blocks_found_alert_active"] = self._blocks_found_alert_active(
                self.snapshot.values
            )
            available = self._available_metrics(self.snapshot.values)
            result["next_metric"] = (
                available[self.metric_index % len(available)]
                if available
                else None
            )
        result["enabled_metrics"] = self.config.enabled_metrics
        result["deployed_commit"] = self.deployed_commit
        return result

    def acknowledge_block_found(self) -> dict[str, int | bool]:
        with self.lock:
            actual = self.snapshot.values.get("blocks_found")
            if actual is None:
                raise ValueError("blocks_found is not currently available")
            actual_count = int(actual)
            acknowledged = self.alert_state.acknowledged_blocks_found
            if acknowledged < actual_count:
                acknowledged += 1
                updated = AlertState(
                    acknowledged_blocks_found=acknowledged,
                    last_flashed_blocks_found=self.alert_state.last_flashed_blocks_found,
                )
                self.state_store.save(updated)
                self.alert_state = updated
            return {
                "blocks_found": actual_count,
                "current_block_counter": acknowledged,
                "blocks_found_alert_active": actual_count > acknowledged,
            }

    def _blocks_found_alert_active(self, values: dict[str, float | int]) -> bool:
        actual = values.get("blocks_found")
        return (
            "blocks_found" in self.config.enabled_metrics
            and actual is not None
            and int(actual) > self.alert_state.acknowledged_blocks_found
        )

    def _available_metrics(self, values: dict[str, float | int]) -> list[str]:
        alert_active = self._blocks_found_alert_active(values)
        return [
            metric
            for metric in self.config.enabled_metrics
            if metric in values and (metric != "blocks_found" or alert_active)
        ]

    def run_once(self) -> None:
        values, errors = self.collector.collect()
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.snapshot.values = values
            self.snapshot.errors = errors
            self.snapshot.refreshed_at = now

        available = self._available_metrics(values)
        if not available:
            raise RuntimeError("no configured metrics were available")

        blocks_found = values.get("blocks_found")
        flash_lights = (
            self._blocks_found_alert_active(values)
            and blocks_found is not None
            and int(blocks_found) > self.alert_state.last_flashed_blocks_found
        )
        if flash_lights:
            metric = "blocks_found"
            self.metric_index = available.index(metric) + 1
        else:
            metric = available[self.metric_index % len(available)]
            self.metric_index += 1
        try:
            self.blockclock.show(metric, values[metric])
            if flash_lights:
                self.blockclock.flash_lights()
                with self.lock:
                    updated = AlertState(
                        acknowledged_blocks_found=(
                            self.alert_state.acknowledged_blocks_found
                        ),
                        last_flashed_blocks_found=int(blocks_found),
                    )
                    self.state_store.save(updated)
                    self.alert_state = updated
        except (OSError, ValueError, urllib.error.URLError) as error:
            with self.lock:
                self.snapshot.display_error = str(error)
            raise
        else:
            with self.lock:
                self.snapshot.last_displayed_metric = metric
                self.snapshot.last_displayed_at = now
                self.snapshot.display_error = None
            LOGGER.info("displayed %s=%s", metric, values[metric])

    def run(self) -> None:
        try:
            self.blockclock.pause_backend_updates()
        except (OSError, urllib.error.URLError) as error:
            LOGGER.warning("could not pause normal Blockclock updates: %s", error)

        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:  # service boundary: log and retry next scheduled interval
                LOGGER.exception("scheduled update failed")
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1, self.config.display_interval_seconds - elapsed))


def make_handler(adapter: Adapter):
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in ("/", "/health", "/status"):
                self.send_error(404)
                return
            payload = json.dumps(adapter.status(), sort_keys=True).encode("utf8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/blocks-found/acknowledge":
                self.send_error(404)
                return
            try:
                result = adapter.acknowledge_block_found()
            except ValueError as error:
                self.send_error(409, str(error))
                return
            payload = json.dumps(result, sort_keys=True).encode("utf8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            LOGGER.debug(format, *args)

    return StatusHandler


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config()
    config.validate()
    adapter = Adapter(config)
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), make_handler(adapter))
    worker = threading.Thread(target=adapter.run, name="display-rotation", daemon=True)

    def stop(*_: Any) -> None:
        adapter.stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.start()
    LOGGER.info("status endpoint listening on %s:%s", config.bind_host, config.bind_port)
    try:
        server.serve_forever()
    finally:
        adapter.stop_event.set()
        worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
