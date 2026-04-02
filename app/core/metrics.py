import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


REQUEST_COUNT = Counter(
    "config_service_http_requests_total",
    "Total HTTP requests processed.",
    ["method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "config_service_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "path"],
)
CONFIG_MUTATIONS = Counter(
    "config_service_config_mutations_total",
    "Config mutations by action.",
    ["action"],
)
ROLLOUT_EVENTS = Counter(
    "config_service_rollout_events_total",
    "Rollout transitions by status.",
    ["status"],
)
CLIENT_FAILURE_REPORTS = Counter(
    "config_service_client_failure_reports_total",
    "Anonymous client failure reports ingested by the control plane.",
    ["target", "source"],
)
ACTIVE_WEBSOCKETS = Gauge(
    "config_service_active_websockets",
    "Number of active websocket connections.",
)
REDIS_AVAILABLE = Gauge(
    "config_service_redis_available",
    "Whether Redis is reachable.",
)


async def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def metrics_middleware(request: Request, call_next: Callable):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    path = request.url.path
    REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response
