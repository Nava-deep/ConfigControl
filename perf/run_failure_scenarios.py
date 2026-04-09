from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import redis
import uvicorn
import websockets

os.environ.setdefault("CONFIG_SERVICE_USE_REDIS", "false")

from app.core.settings import Settings
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "perf" / "results"
ADMIN_HEADERS = {"X-User-Id": "failure-admin", "X-Role": "admin"}
READER_HEADERS = {"X-User-Id": "failure-reader", "X-Role": "reader"}
SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
    "required": ["timeout_ms"],
    "additionalProperties": False,
}
FAILURE_METRICS = [
    "config_service_config_rollback_total",
    "config_service_validation_failures_total",
    "config_service_redis_fallback_total",
    "config_service_websocket_updates_total",
    "config_service_longpoll_updates_total",
]


@dataclass
class FailureScenarioResult:
    name: str
    status: str
    duration_ms: float
    error_count: int
    observed_latency_ms: float | None = None
    propagation_latency_ms: float | None = None
    notes: str = ""
    metrics_delta: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_iso_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_timestamp(payload: dict[str, Any]) -> datetime:
    raw = payload.get("published_at") or payload.get("timestamp")
    if not isinstance(raw, str):
        raise KeyError("event payload is missing both 'published_at' and 'timestamp'")
    return parse_iso_timestamp(raw)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ScenarioServer:
    def __init__(self, **settings_overrides: Any) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="config-control-plane-failure-")
        temp_root = Path(self.tempdir.name)
        defaults = {
            "host": "127.0.0.1",
            "port": find_free_port(),
            "database_url": f"sqlite:///{temp_root / 'failure-scenarios.db'}",
            "use_redis": False,
            "sdk_cache_dir": temp_root / ".sdk-cache",
            "canary_poll_interval_seconds": 0.05,
            "longpoll_timeout_seconds": 1,
            "log_level": "ERROR",
        }
        defaults.update(settings_overrides)
        self.settings = Settings(**defaults)
        self.app = create_app(self.settings)
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.settings.host,
                port=self.settings.port,
                log_level="warning",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self._run, name="failure-scenario-uvicorn", daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.settings.host}:{self.settings.port}"

    def _run(self) -> None:
        asyncio.run(self.server.serve())

    async def start(self) -> None:
        self.thread.start()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=2.0) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    response = await client.get("/health/live")
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    await asyncio.sleep(0.05)
                    continue
                await asyncio.sleep(0.05)
        raise RuntimeError("failure scenario server did not start within 10 seconds")

    async def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        self.tempdir.cleanup()

    def current_sequence(self) -> int:
        return int(self.app.state.container.notifications._sequence)  # noqa: SLF001


async def create_version(
    client: httpx.AsyncClient,
    *,
    name: str,
    value: int,
    include_schema: bool = False,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "name": name,
        "environment": "prod",
        "value": {"timeout_ms": value},
    }
    if include_schema:
        payload["schema"] = SCHEMA
    return await client.post("/configs", headers=ADMIN_HEADERS, json=payload)


def metric_labels(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    labels: dict[str, str] = {}
    for item in raw.split(","):
        if not item:
            continue
        key, value = item.split("=", 1)
        labels[key] = value.strip('"')
    return labels


def parse_metrics(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        metric_name, remainder = line.split(" ", 1)
        labels: dict[str, str] = {}
        if "{" in metric_name:
            name, label_blob = metric_name.split("{", 1)
            metric_name = name
            labels = metric_labels(label_blob.rstrip("}"))
        samples[(metric_name, tuple(sorted(labels.items())))] = float(remainder.strip())
    return samples


async def scrape_metrics(client: httpx.AsyncClient) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    response = await client.get("/metrics")
    response.raise_for_status()
    return parse_metrics(response.text)


def metric_delta(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    metric_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keys = {key for key in set(before) | set(after) if key[0] == metric_name}
    for key in sorted(keys):
        labels = dict(key[1])
        rows.append(
            {
                "labels": labels,
                "delta": round(after.get(key, 0.0) - before.get(key, 0.0), 6),
            }
        )
    return rows


def collect_relevant_metrics(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
) -> dict[str, list[dict[str, Any]]]:
    return {metric_name: metric_delta(before, after, metric_name) for metric_name in FAILURE_METRICS}


def format_optional(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


class PublishFailRedisClient:
    def get(self, key: str):
        return None

    def set(self, key: str, value: str, ex: int | None = None):
        return True

    def publish(self, channel: str, payload: str):
        raise redis.RedisError(f"simulated publish failure for {channel}")


class DelayedFailingRedisClient(PublishFailRedisClient):
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    def get(self, key: str):
        time.sleep(self.delay_seconds)
        raise redis.RedisError(f"simulated delayed get failure for {key}")


async def scenario_redis_unavailable_startup() -> FailureScenarioResult:
    server = ScenarioServer(use_redis=True, redis_url="redis://127.0.0.1:1/0")
    started = time.perf_counter()
    error_count = 0
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=5.0) as client:
            ready = await client.get("/health/ready")
            ready.raise_for_status()
            if ready.json()["redis"] is not False:
                raise AssertionError("expected ready endpoint to report redis unavailable")

            created = await create_version(
                client,
                name="checkout-service.redis-outage-startup",
                value=2000,
                include_schema=True,
            )
            created.raise_for_status()

            resolved = await client.get(
                "/configs/checkout-service.redis-outage-startup",
                headers=READER_HEADERS,
                params={"environment": "prod", "target": "checkout-service", "client_id": "startup-check"},
            )
            resolved.raise_for_status()
            metrics_after = await scrape_metrics(client)
        metrics_delta = collect_relevant_metrics({}, metrics_after)
        notes = "Service started with Redis unavailable, reported redis=false on readiness, and still served config writes/reads."
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="redis_unavailable_at_startup",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        notes=notes,
        metrics_delta=metrics_delta,
    )


async def scenario_invalid_schema_publish() -> FailureScenarioResult:
    server = ScenarioServer()
    started = time.perf_counter()
    error_count = 0
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=5.0) as client:
            metrics_before = await scrape_metrics(client)
            response = await client.post(
                "/configs",
                headers=ADMIN_HEADERS,
                json={
                    "name": "checkout-service.invalid-schema",
                    "environment": "prod",
                    "schema": {"type": "definitely-not-a-valid-jsonschema-type"},
                    "value": {"timeout_ms": 1000},
                },
            )
            if response.status_code != 422:
                raise AssertionError(f"expected 422 for invalid schema, got {response.status_code}: {response.text}")
            metrics_after = await scrape_metrics(client)
        metrics_delta = collect_relevant_metrics(metrics_before, metrics_after)
        notes = "Malformed JSON Schema definitions are rejected before a version is created."
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="invalid_schema_publish_rejected",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        notes=notes,
        metrics_delta=metrics_delta,
    )


async def scenario_websocket_delivery_publish_failure() -> FailureScenarioResult:
    server = ScenarioServer()
    started = time.perf_counter()
    error_count = 0
    propagation_latency_ms: float | None = None
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            assert (await create_version(client, name="checkout-service.ws-failure", value=2000, include_schema=True)).status_code == 201
            assert (await create_version(client, name="checkout-service.ws-failure", value=2800)).status_code == 201
            container = server.app.state.container
            container.cache.client = PublishFailRedisClient()
            container.cache._set_available(True)  # noqa: SLF001
            metrics_before = await scrape_metrics(client)

            ws_url = (
                server.base_url.replace("http://", "ws://", 1)
                + "/watch/ws?config_name=checkout-service.ws-failure&environment=prod&target=checkout-service"
            )
            async with websockets.connect(ws_url, additional_headers=READER_HEADERS) as websocket:
                await websocket.recv()
                rollout = await client.post(
                    "/configs/checkout-service.ws-failure/rollout",
                    headers=ADMIN_HEADERS,
                    json={"target": "checkout-service", "environment": "prod", "percent": 10},
                )
                rollout.raise_for_status()
                payload = json.loads(await websocket.recv())
                propagation_latency_ms = (now_utc() - event_timestamp(payload)).total_seconds() * 1000
            metrics_after = await scrape_metrics(client)
            if container.cache.is_available():
                raise AssertionError("expected cache availability to drop after simulated redis publish failure")
        metrics_delta = collect_relevant_metrics(metrics_before, metrics_after)
        notes = "Local websocket delivery continued even after the Redis publish step failed."
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="websocket_delivery_with_publish_failure",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        propagation_latency_ms=propagation_latency_ms,
        notes=notes,
        metrics_delta=metrics_delta,
    )


async def scenario_longpoll_delivery_publish_failure() -> FailureScenarioResult:
    server = ScenarioServer()
    started = time.perf_counter()
    error_count = 0
    propagation_latency_ms: float | None = None
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            assert (await create_version(client, name="checkout-service.longpoll-failure", value=2000, include_schema=True)).status_code == 201
            assert (await create_version(client, name="checkout-service.longpoll-failure", value=3200)).status_code == 201
            container = server.app.state.container
            container.cache.client = PublishFailRedisClient()
            container.cache._set_available(True)  # noqa: SLF001
            last_sequence = server.current_sequence()
            metrics_before = await scrape_metrics(client)

            task = asyncio.create_task(
                client.get(
                    "/watch/longpoll",
                    headers=READER_HEADERS,
                    params={
                        "last_sequence": last_sequence,
                        "config_name": "checkout-service.longpoll-failure",
                        "environment": "prod",
                        "target": "checkout-service",
                        "timeout": 5,
                    },
                )
            )
            await asyncio.sleep(0.02)
            rollout = await client.post(
                "/configs/checkout-service.longpoll-failure/rollout",
                headers=ADMIN_HEADERS,
                json={"target": "checkout-service", "environment": "prod", "percent": 10},
            )
            rollout.raise_for_status()
            response = await task
            response.raise_for_status()
            propagation_latency_ms = (now_utc() - event_timestamp(response.json())).total_seconds() * 1000
            metrics_after = await scrape_metrics(client)
            if container.cache.is_available():
                raise AssertionError("expected cache availability to drop after simulated redis publish failure")
        metrics_delta = collect_relevant_metrics(metrics_before, metrics_after)
        notes = "Long-poll delivery continued from the in-process notification hub during Redis publish failure."
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="longpoll_delivery_with_publish_failure",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        propagation_latency_ms=propagation_latency_ms,
        notes=notes,
        metrics_delta=metrics_delta,
    )


async def scenario_rollback_publish_failure() -> FailureScenarioResult:
    server = ScenarioServer()
    started = time.perf_counter()
    error_count = 0
    observed_latency_ms: float | None = None
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            assert (await create_version(client, name="checkout-service.rollback-failure", value=2000, include_schema=True)).status_code == 201
            assert (await create_version(client, name="checkout-service.rollback-failure", value=3600)).status_code == 201
            promoted = await client.post(
                "/configs/checkout-service.rollback-failure/rollout",
                headers=ADMIN_HEADERS,
                json={"target": "checkout-service", "environment": "prod", "percent": 100},
            )
            promoted.raise_for_status()

            container = server.app.state.container
            container.cache.client = PublishFailRedisClient()
            container.cache._set_available(True)  # noqa: SLF001
            metrics_before = await scrape_metrics(client)

            rollback_started = time.perf_counter()
            rollback = await client.post(
                "/configs/checkout-service.rollback-failure/rollback",
                headers=ADMIN_HEADERS,
                json={"target": "checkout-service", "environment": "prod", "target_version": 1},
            )
            rollback.raise_for_status()
            observed_latency_ms = (time.perf_counter() - rollback_started) * 1000

            resolved = await client.get(
                "/configs/checkout-service.rollback-failure",
                headers=READER_HEADERS,
                params={"environment": "prod", "target": "checkout-service", "client_id": "rollback-check"},
            )
            resolved.raise_for_status()
            if resolved.json()["version"] != 1:
                raise AssertionError("rollback did not restore the stable version")
            metrics_after = await scrape_metrics(client)
        metrics_delta = collect_relevant_metrics(metrics_before, metrics_after)
        notes = "Rollback completed successfully and restored the stable version even when Redis publish failed."
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="rollback_with_publish_failure",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        observed_latency_ms=observed_latency_ms,
        notes=notes,
        metrics_delta=metrics_delta,
    )


async def scenario_delayed_redis_get_fallback(delay_seconds: float) -> FailureScenarioResult:
    server = ScenarioServer()
    started = time.perf_counter()
    error_count = 0
    observed_latency_ms: float | None = None
    try:
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            assert (await create_version(client, name="checkout-service.delayed-fallback", value=2400, include_schema=True)).status_code == 201
            container = server.app.state.container
            container.cache.client = DelayedFailingRedisClient(delay_seconds)
            container.cache._set_available(True)  # noqa: SLF001
            metrics_before = await scrape_metrics(client)

            fetch_started = time.perf_counter()
            response = await client.get(
                "/configs/checkout-service.delayed-fallback",
                headers=READER_HEADERS,
                params={"version": 1, "environment": "prod", "target": "checkout-service"},
            )
            response.raise_for_status()
            observed_latency_ms = (time.perf_counter() - fetch_started) * 1000
            if response.json()["version"] != 1:
                raise AssertionError("delayed fallback fetch returned the wrong version")
            metrics_after = await scrape_metrics(client)
        metrics_delta = collect_relevant_metrics(metrics_before, metrics_after)
        notes = (
            "A delayed Redis read failure increased fetch latency but the request still succeeded "
            "from the in-memory fallback path."
        )
        status = "passed"
    except Exception as exc:
        error_count = 1
        metrics_delta = {}
        notes = str(exc)
        status = "failed"
    finally:
        await server.stop()
    return FailureScenarioResult(
        name="delayed_redis_get_fallback",
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_count=error_count,
        observed_latency_ms=observed_latency_ms,
        notes=notes,
        metrics_delta=metrics_delta,
    )


def build_report(*, started_at: datetime, finished_at: datetime, results: list[FailureScenarioResult]) -> dict[str, Any]:
    passed = sum(1 for item in results if item.status == "passed")
    failed = len(results) - passed
    total_error_count = sum(item.error_count for item in results)
    return {
        "run_started_at": started_at.isoformat(),
        "run_finished_at": finished_at.isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "database": "sqlite",
            "redis": "synthetic failure injection",
        },
        "scenario_count": len(results),
        "passed_count": passed,
        "failed_count": failed,
        "error_count": total_error_count,
        "scenarios": [asdict(item) for item in results],
    }


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"failure_{timestamp}.json"
    markdown_path = output_dir / f"failure_{timestamp}.md"
    latest_json = output_dir / "latest_failure_results.json"
    latest_markdown = output_dir / "latest_failure_report.md"

    json_text = json.dumps(report, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")

    scenario_rows = "\n".join(
        f"| {item['name']} | {item['status']} | {item['duration_ms']:.2f} | "
        f"{format_optional(item['observed_latency_ms'])} | "
        f"{format_optional(item['propagation_latency_ms'])} | "
        f"{item['error_count']} | {item['notes']} |"
        for item in report["scenarios"]
    )

    metric_sections: list[str] = []
    for item in report["scenarios"]:
        metric_sections.append(f"### {item['name']}")
        metric_sections.append("")
        metric_sections.append("| Metric | Labels | Delta |")
        metric_sections.append("| --- | --- | ---: |")
        metrics_delta: dict[str, list[dict[str, Any]]] = item["metrics_delta"]
        if not any(metrics_delta.values()):
            metric_sections.append("| n/a | n/a | 0 |")
        else:
            for metric_name, rows in metrics_delta.items():
                if not rows:
                    continue
                for row in rows:
                    label_summary = ", ".join(f"{key}={value}" for key, value in row["labels"].items()) or "-"
                    metric_sections.append(f"| {metric_name} | {label_summary} | {row['delta']} |")
        metric_sections.append("")

    markdown_text = "\n".join(
        [
            "# Failure Scenario Report",
            "",
            f"Generated at: `{report['run_finished_at']}`",
            "",
            "## Environment",
            "",
            f"- Python: `{report['environment']['python']}`",
            f"- Platform: `{report['environment']['platform']}`",
            f"- Database: `{report['environment']['database']}`",
            f"- Redis mode: `{report['environment']['redis']}`",
            f"- Scenario count: `{report['scenario_count']}`",
            f"- Passed: `{report['passed_count']}`",
            f"- Failed: `{report['failed_count']}`",
            f"- Error count: `{report['error_count']}`",
            "",
            "## Scenario Summary",
            "",
            "| Scenario | Status | Duration (ms) | Observed Latency (ms) | Propagation Latency (ms) | Error Count | Notes |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            scenario_rows,
            "",
            "## Metrics Delta",
            "",
            *metric_sections,
        ]
    ).strip() + "\n"
    markdown_path.write_text(markdown_text, encoding="utf-8")
    latest_markdown.write_text(markdown_text, encoding="utf-8")
    return latest_json, latest_markdown


async def run(args: argparse.Namespace) -> tuple[Path, Path]:
    started_at = now_utc()
    results = [
        await scenario_redis_unavailable_startup(),
        await scenario_invalid_schema_publish(),
        await scenario_websocket_delivery_publish_failure(),
        await scenario_longpoll_delivery_publish_failure(),
        await scenario_rollback_publish_failure(),
        await scenario_delayed_redis_get_fallback(args.delay_seconds),
    ]
    finished_at = now_utc()
    report = build_report(started_at=started_at, finished_at=finished_at, results=results)
    return write_reports(report, args.output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible failure-scenario validation for Config Control Plane.")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.05,
        help="Injected latency for the delayed Redis get failure scenario.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where JSON and Markdown failure reports will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    latest_json, latest_markdown = asyncio.run(run(args))
    print(f"Failure scenario JSON report: {latest_json}")
    print(f"Failure scenario Markdown report: {latest_markdown}")


if __name__ == "__main__":
    main()
