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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import websockets

os.environ.setdefault("CONFIG_SERVICE_USE_REDIS", "false")

from app.core.settings import Settings
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "perf" / "results"
ADMIN_HEADERS = {"X-User-Id": "benchmark-admin", "X-Role": "admin"}
READER_HEADERS = {"X-User-Id": "benchmark-reader", "X-Role": "reader"}
SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
    "required": ["timeout_ms"],
    "additionalProperties": False,
}


@dataclass
class BenchmarkSummary:
    name: str
    count: int
    failures: int
    average_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float
    notes: str


def percentile(samples: list[float], value: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * value
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize(name: str, samples: list[float], notes: str, failures: int = 0) -> BenchmarkSummary:
    if not samples:
        raise RuntimeError(f"benchmark '{name}' produced no samples")
    return BenchmarkSummary(
        name=name,
        count=len(samples),
        failures=failures,
        average_ms=sum(samples) / len(samples),
        p95_ms=percentile(samples, 0.95),
        minimum_ms=min(samples),
        maximum_ms=max(samples),
        notes=notes,
    )


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


class BenchmarkServer:
    def __init__(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="config-control-plane-bench-")
        temp_root = Path(self.tempdir.name)
        self.port = find_free_port()
        self.settings = Settings(
            host="127.0.0.1",
            port=self.port,
            database_url=f"sqlite:///{temp_root / 'benchmark.db'}",
            use_redis=False,
            sdk_cache_dir=temp_root / ".sdk-cache",
            canary_poll_interval_seconds=0.05,
            longpoll_timeout_seconds=1,
            log_level="WARNING",
        )
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
        self.thread = threading.Thread(target=self._run, name="benchmark-uvicorn", daemon=True)

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
        raise RuntimeError("benchmark server did not start within 10 seconds")

    async def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        self.tempdir.cleanup()

    def current_sequence(self) -> int:
        return int(self.app.state.container.notifications._sequence)  # noqa: SLF001

    def clear_config_cache(self) -> None:
        cache = self.app.state.container.cache  # noqa: SLF001
        for key in list(cache._memory_store):  # noqa: SLF001
            if key.startswith("config:"):
                cache._memory_store.pop(key, None)  # noqa: SLF001


async def create_version(
    client: httpx.AsyncClient,
    *,
    name: str,
    value: int,
    environment: str = "prod",
    include_schema: bool = False,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "name": name,
        "environment": environment,
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


async def benchmark_publish_latency(client: httpx.AsyncClient, iterations: int) -> BenchmarkSummary:
    samples: list[float] = []
    for index in range(iterations):
        name = f"checkout-service.publish-{index}"
        baseline = await create_version(client, name=name, value=1000 + index, include_schema=True)
        baseline.raise_for_status()

        started = time.perf_counter()
        response = await create_version(client, name=name, value=2000 + index)
        response.raise_for_status()
        samples.append((time.perf_counter() - started) * 1000)
    return summarize(
        "publish_latency_ms",
        samples,
        notes="Measures version publish latency for a new immutable config version after a baseline version already exists.",
    )


async def benchmark_resolved_fetch_latency(client: httpx.AsyncClient, iterations: int) -> BenchmarkSummary:
    name = "checkout-service.fetch-latency"
    baseline = await create_version(client, name=name, value=1800, include_schema=True)
    baseline.raise_for_status()

    samples: list[float] = []
    for index in range(iterations):
        started = time.perf_counter()
        response = await client.get(
            f"/configs/{name}",
            headers=READER_HEADERS,
            params={
                "version": "resolved",
                "environment": "prod",
                "target": "checkout-service",
                "client_id": f"fetch-reader-{index}",
            },
        )
        response.raise_for_status()
        samples.append((time.perf_counter() - started) * 1000)
    return summarize(
        "resolved_fetch_latency_ms",
        samples,
        notes="Measures resolved config fetch latency with stable target resolution enabled.",
    )


async def benchmark_cached_vs_uncached_fetch(
    client: httpx.AsyncClient,
    server: BenchmarkServer,
    iterations: int,
) -> tuple[BenchmarkSummary, BenchmarkSummary]:
    name = "checkout-service.cache-bench"
    baseline = await create_version(client, name=name, value=2100, include_schema=True)
    baseline.raise_for_status()

    uncached: list[float] = []
    for _ in range(iterations):
        server.clear_config_cache()
        started = time.perf_counter()
        response = await client.get(
            f"/configs/{name}",
            headers=READER_HEADERS,
            params={"version": 1, "environment": "prod", "target": "checkout-service"},
        )
        response.raise_for_status()
        uncached.append((time.perf_counter() - started) * 1000)

    warm = await client.get(
        f"/configs/{name}",
        headers=READER_HEADERS,
        params={"version": 1, "environment": "prod", "target": "checkout-service"},
    )
    warm.raise_for_status()

    cached: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        response = await client.get(
            f"/configs/{name}",
            headers=READER_HEADERS,
            params={"version": 1, "environment": "prod", "target": "checkout-service"},
        )
        response.raise_for_status()
        cached.append((time.perf_counter() - started) * 1000)

    return (
        summarize(
            "uncached_fetch_latency_ms",
            uncached,
            notes="Measures explicit version fetch latency after clearing the in-memory config cache between requests.",
        ),
        summarize(
            "cached_fetch_latency_ms",
            cached,
            notes="Measures explicit version fetch latency after warming the in-memory config cache.",
        ),
    )


async def benchmark_rollback_latency(client: httpx.AsyncClient, iterations: int) -> BenchmarkSummary:
    samples: list[float] = []
    for index in range(iterations):
        name = f"checkout-service.rollback-{index}"
        baseline = await create_version(client, name=name, value=1200 + index, include_schema=True)
        baseline.raise_for_status()
        candidate = await create_version(client, name=name, value=3200 + index)
        candidate.raise_for_status()
        rollout = await client.post(
            f"/configs/{name}/rollout",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "percent": 100},
        )
        rollout.raise_for_status()

        started = time.perf_counter()
        response = await client.post(
            f"/configs/{name}/rollback",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "target_version": 1},
        )
        response.raise_for_status()
        samples.append((time.perf_counter() - started) * 1000)
    return summarize(
        "rollback_latency_ms",
        samples,
        notes="Measures rollback latency after promoting a candidate version to stable.",
    )


async def benchmark_websocket_delivery(client: httpx.AsyncClient, iterations: int, base_url: str) -> BenchmarkSummary:
    samples: list[float] = []
    ws_base = base_url.replace("http://", "ws://", 1)

    for index in range(iterations):
        name = f"checkout-service.websocket-{index}"
        baseline = await create_version(client, name=name, value=1400 + index, include_schema=True)
        baseline.raise_for_status()
        candidate = await create_version(client, name=name, value=2400 + index)
        candidate.raise_for_status()

        ws_url = (
            f"{ws_base}/watch/ws"
            f"?config_name={name}&environment=prod&target=checkout-service"
        )
        async with websockets.connect(ws_url, additional_headers=READER_HEADERS) as websocket:
            await websocket.recv()
            rollout = await client.post(
                f"/configs/{name}/rollout",
                headers=ADMIN_HEADERS,
                json={"target": "checkout-service", "environment": "prod", "percent": 10},
            )
            rollout.raise_for_status()
            payload = json.loads(await websocket.recv())
            received_at = now_utc()
            published_at = event_timestamp(payload)
            samples.append((received_at - published_at).total_seconds() * 1000)
    return summarize(
        "websocket_delivery_latency_ms",
        samples,
        notes="Measures event propagation from server publish timestamp to websocket client receipt for rollout events.",
    )


async def benchmark_longpoll_delivery(
    client: httpx.AsyncClient,
    server: BenchmarkServer,
    iterations: int,
) -> BenchmarkSummary:
    samples: list[float] = []

    for index in range(iterations):
        name = f"checkout-service.longpoll-{index}"
        baseline = await create_version(client, name=name, value=1600 + index, include_schema=True)
        baseline.raise_for_status()
        candidate = await create_version(client, name=name, value=2600 + index)
        candidate.raise_for_status()
        last_sequence = server.current_sequence()

        task = asyncio.create_task(
            client.get(
                "/watch/longpoll",
                headers=READER_HEADERS,
                params={
                    "last_sequence": last_sequence,
                    "config_name": name,
                    "environment": "prod",
                    "target": "checkout-service",
                    "timeout": 5,
                },
            )
        )
        await asyncio.sleep(0.02)
        rollout = await client.post(
            f"/configs/{name}/rollout",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "percent": 10},
        )
        rollout.raise_for_status()
        response = await task
        response.raise_for_status()
        received_at = now_utc()
        payload = response.json()
        published_at = event_timestamp(payload)
        samples.append((received_at - published_at).total_seconds() * 1000)

    return summarize(
        "longpoll_delivery_latency_ms",
        samples,
        notes="Measures event propagation from server publish timestamp to long-poll response receipt for rollout events.",
    )


async def benchmark_longpoll_timeout(client: httpx.AsyncClient, iterations: int) -> BenchmarkSummary:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        response = await client.get(
            "/watch/longpoll",
            headers=READER_HEADERS,
            params={
                "last_sequence": 0,
                "config_name": "checkout-service.timeout",
                "environment": "prod",
                "target": "checkout-service",
                "timeout": 0.2,
            },
        )
        assert response.status_code == 204
        samples.append((time.perf_counter() - started) * 1000)

    return summarize(
        "longpoll_timeout_duration_ms",
        samples,
        notes="Measures how closely idle long-poll requests track the requested timeout when no update arrives.",
    )


async def benchmark_concurrent_fetch_load(
    client: httpx.AsyncClient,
    *,
    concurrency: int,
    requests_per_worker: int,
) -> BenchmarkSummary:
    name = "checkout-service.concurrent"
    baseline = await create_version(client, name=name, value=3000, include_schema=True)
    baseline.raise_for_status()

    samples: list[float] = []

    async def worker(worker_index: int) -> list[float]:
        worker_samples: list[float] = []
        for request_index in range(requests_per_worker):
            started = time.perf_counter()
            response = await client.get(
                f"/configs/{name}",
                headers=READER_HEADERS,
                params={
                    "version": "resolved",
                    "environment": "prod",
                    "target": "checkout-service",
                    "client_id": f"worker-{worker_index}-request-{request_index}",
                },
            )
            response.raise_for_status()
            worker_samples.append((time.perf_counter() - started) * 1000)
        return worker_samples

    started = time.perf_counter()
    for result in await asyncio.gather(*[worker(index) for index in range(concurrency)]):
        samples.extend(result)
    elapsed = time.perf_counter() - started
    throughput = len(samples) / elapsed if elapsed > 0 else 0.0

    return summarize(
        "concurrent_fetch_latency_ms",
        samples,
        notes=(
            "Measures resolved fetch latency under concurrent synthetic load. "
            f"Throughput during this run: {throughput:.2f} requests/second across {concurrency} workers."
        ),
    )


def build_report(
    *,
    started_at: datetime,
    finished_at: datetime,
    iterations: int,
    delivery_iterations: int,
    concurrency: int,
    requests_per_worker: int,
    benchmarks: list[BenchmarkSummary],
    metrics_before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    metrics_after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
) -> dict[str, Any]:
    metric_names = [
        "config_service_config_fetch_total",
        "config_service_config_publish_total",
        "config_service_config_rollback_total",
        "config_service_cache_hits_total",
        "config_service_cache_misses_total",
        "config_service_websocket_updates_total",
        "config_service_longpoll_updates_total",
        "config_service_redis_fallback_total",
    ]
    metrics_delta_report = {
        metric_name: metric_delta(metrics_before, metrics_after, metric_name)
        for metric_name in metric_names
    }

    return {
        "run_started_at": started_at.isoformat(),
        "run_finished_at": finished_at.isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "database": "sqlite",
            "redis": "disabled",
        },
        "parameters": {
            "iterations": iterations,
            "delivery_iterations": delivery_iterations,
            "concurrency": concurrency,
            "requests_per_worker": requests_per_worker,
        },
        "benchmarks": [asdict(item) for item in benchmarks],
        "metrics_delta": metrics_delta_report,
    }


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"benchmark_{timestamp}.json"
    markdown_path = output_dir / f"benchmark_{timestamp}.md"
    latest_json = output_dir / "latest_results.json"
    latest_markdown = output_dir / "latest_report.md"

    json_text = json.dumps(report, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")

    rows = "\n".join(
        f"| {item['name']} | {item['count']} | {item['average_ms']:.2f} | {item['p95_ms']:.2f} | {item['failures']} | {item['notes']} |"
        for item in report["benchmarks"]
    )
    metrics_rows: list[str] = []
    for metric_name, entries in report["metrics_delta"].items():
        if not entries:
            metrics_rows.append(f"| {metric_name} | n/a | 0 |")
            continue
        for entry in entries:
            label_summary = ", ".join(f"{key}={value}" for key, value in entry["labels"].items()) or "-"
            metrics_rows.append(f"| {metric_name} | {label_summary} | {entry['delta']} |")

    markdown_text = "\n".join(
        [
            "# Benchmark Report",
            "",
            f"Generated at: `{report['run_finished_at']}`",
            "",
            "## Environment",
            "",
            f"- Python: `{report['environment']['python']}`",
            f"- Platform: `{report['environment']['platform']}`",
            f"- Database: `{report['environment']['database']}`",
            f"- Redis: `{report['environment']['redis']}`",
            f"- Iterations: `{report['parameters']['iterations']}`",
            f"- Delivery iterations: `{report['parameters']['delivery_iterations']}`",
            f"- Concurrency: `{report['parameters']['concurrency']}`",
            f"- Requests per worker: `{report['parameters']['requests_per_worker']}`",
            "",
            "## Benchmark Summary",
            "",
            "| Benchmark | Samples | Average (ms) | p95 (ms) | Failures | Notes |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
            rows,
            "",
            "## Metrics Delta",
            "",
            "| Metric | Labels | Delta |",
            "| --- | --- | ---: |",
            "\n".join(metrics_rows),
        ]
    ).strip() + "\n"
    markdown_path.write_text(markdown_text, encoding="utf-8")
    latest_markdown.write_text(markdown_text, encoding="utf-8")
    return latest_json, latest_markdown


async def run(args: argparse.Namespace) -> tuple[Path, Path]:
    server = BenchmarkServer()
    await server.start()
    started_at = now_utc()
    try:
        limits = httpx.Limits(max_connections=max(args.concurrency * 2, 20), max_keepalive_connections=max(args.concurrency, 10))
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10.0, limits=limits) as client:
            metrics_before = await scrape_metrics(client)

            publish = await benchmark_publish_latency(client, args.iterations)
            resolved_fetch = await benchmark_resolved_fetch_latency(client, args.iterations)
            uncached_fetch, cached_fetch = await benchmark_cached_vs_uncached_fetch(client, server, args.iterations)
            rollback = await benchmark_rollback_latency(client, args.iterations)
            websocket_delivery = await benchmark_websocket_delivery(client, args.delivery_iterations, server.base_url)
            longpoll_delivery = await benchmark_longpoll_delivery(client, server, args.delivery_iterations)
            longpoll_timeout = await benchmark_longpoll_timeout(client, args.delivery_iterations)
            concurrent_fetch = await benchmark_concurrent_fetch_load(
                client,
                concurrency=args.concurrency,
                requests_per_worker=args.requests_per_worker,
            )

            metrics_after = await scrape_metrics(client)
        finished_at = now_utc()
    finally:
        await server.stop()

    report = build_report(
        started_at=started_at,
        finished_at=finished_at,
        iterations=args.iterations,
        delivery_iterations=args.delivery_iterations,
        concurrency=args.concurrency,
        requests_per_worker=args.requests_per_worker,
        benchmarks=[
            publish,
            resolved_fetch,
            uncached_fetch,
            cached_fetch,
            rollback,
            websocket_delivery,
            longpoll_delivery,
            longpoll_timeout,
            concurrent_fetch,
        ],
        metrics_before=metrics_before,
        metrics_after=metrics_after,
    )
    return write_reports(report, args.output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible local benchmarks for Config Control Plane.")
    parser.add_argument("--iterations", type=int, default=10, help="Number of iterations for fetch/publish/rollback benchmarks.")
    parser.add_argument(
        "--delivery-iterations",
        type=int,
        default=5,
        help="Number of iterations for websocket and long-poll delivery benchmarks.",
    )
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent workers for the synthetic fetch load test.")
    parser.add_argument("--requests-per-worker", type=int, default=10, help="Requests each concurrent worker should execute.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where JSON and Markdown benchmark reports will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    latest_json, latest_markdown = asyncio.run(run(args))
    print(f"Benchmark JSON report: {latest_json}")
    print(f"Benchmark Markdown report: {latest_markdown}")


if __name__ == "__main__":
    main()
