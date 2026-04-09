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
    ["action", "environment"],
)
ROLLOUT_EVENTS = Counter(
    "config_service_rollout_events_total",
    "Rollout transitions by status.",
    ["status", "environment"],
)
CONFIG_FETCHES = Counter(
    "config_service_config_fetches_total",
    "Config fetches by source and result.",
    ["source", "environment", "result"],
)
CONFIG_FETCH_TOTAL = Counter(
    "config_service_config_fetch_total",
    "Total config fetch operations by source, environment, and result.",
    ["source", "environment", "result"],
)
CONFIG_FETCH_LATENCY = Histogram(
    "config_service_config_fetch_latency_seconds",
    "Latency of config fetch operations.",
    ["source", "environment", "result"],
)
CONFIG_PUBLISH_TOTAL = Counter(
    "config_service_config_publish_total",
    "Total config publish operations.",
    ["environment", "result"],
)
CONFIG_PUBLISH_LATENCY = Histogram(
    "config_service_config_publish_latency_seconds",
    "Latency of config publish operations.",
    ["environment", "result"],
)
CONFIG_ROLLBACK_TOTAL = Counter(
    "config_service_config_rollback_total",
    "Total config rollback operations.",
    ["environment", "trigger", "result"],
)
VALIDATION_FAILURES = Counter(
    "config_service_validation_failures_total",
    "Validation failures by operation.",
    ["operation", "environment"],
)
ROLLOUT_EVALUATIONS = Counter(
    "config_service_rollout_evaluations_total",
    "Background rollout evaluations by outcome.",
    ["outcome", "environment"],
)
DELIVERY_EVENTS = Counter(
    "config_service_delivery_events_total",
    "Notification delivery events by transport and outcome.",
    ["transport", "outcome"],
)
CACHE_EVENTS = Counter(
    "config_service_cache_events_total",
    "Cache and fallback behavior by backend, operation, and outcome.",
    ["backend", "operation", "outcome"],
)
CACHE_HITS_TOTAL = Counter(
    "config_service_cache_hits_total",
    "Total cache hits by backend.",
    ["backend"],
)
CACHE_MISSES_TOTAL = Counter(
    "config_service_cache_misses_total",
    "Total cache misses by backend.",
    ["backend"],
)
REDIS_FALLBACK_TOTAL = Counter(
    "config_service_redis_fallback_total",
    "Total times the service fell back after a Redis failure.",
    ["operation"],
)
CLIENT_FAILURE_REPORTS = Counter(
    "config_service_client_failure_reports_total",
    "Anonymous client failure reports ingested by the control plane.",
    ["target", "source", "environment"],
)
ACTIVE_WEBSOCKETS = Gauge(
    "config_service_active_websockets",
    "Number of active websocket connections.",
)
REDIS_AVAILABLE = Gauge(
    "config_service_redis_available",
    "Whether Redis is reachable.",
)
STARTUP_DEPENDENCY_STATUS = Gauge(
    "config_service_startup_dependency_status",
    "Dependency status during startup checks.",
    ["dependency"],
)
WEBSOCKET_UPDATES_TOTAL = Counter(
    "config_service_websocket_updates_total",
    "Total websocket update attempts by outcome.",
    ["outcome"],
)
LONGPOLL_UPDATES_TOTAL = Counter(
    "config_service_longpoll_updates_total",
    "Total long-poll update responses by outcome.",
    ["outcome"],
)
CONFIG_DELIVERY_LATENCY = Histogram(
    "config_service_config_delivery_latency_seconds",
    "Latency from event publish to client delivery.",
    ["transport", "outcome"],
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
