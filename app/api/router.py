from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.responses import JSONResponse

from app.core.container import ServiceContainer
from app.core.metrics import prometheus_metrics
from app.core.security import Actor, get_actor, get_websocket_actor, require_role
from app.schemas.audit import AuditEntryResponse
from app.schemas.config import (
    ConfigCreateRequest,
    ConfigReadResponse,
    ConfigSummary,
    ConfigVersionResponse,
    DryRunMigrationRequest,
    DryRunMigrationResponse,
    NotificationEvent,
    RollbackRequest,
    RolloutRequest,
    RolloutResponse,
    SimulationMetricResponse,
    SimulationMetricUpdate,
    VersionHistoryEntry,
)
from app.schemas.telemetry import (
    FailureTelemetryEventResponse,
    FailureTelemetryRequest,
    FailureTelemetryResponse,
    FailureTelemetrySummaryResponse,
)

router = APIRouter()


def container_from_request(request: Request) -> ServiceContainer:
    return request.app.state.container


def container_from_ws(websocket: WebSocket) -> ServiceContainer:
    return websocket.app.state.container


@router.get("/health/live")
async def live_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready_health(request: Request) -> dict[str, bool | str]:
    container = container_from_request(request)
    try:
        database_ok = container.database.ping()
    except Exception:
        database_ok = False
    payload = {"status": "ready" if database_ok else "degraded", "database": database_ok, "redis": container.config_service.redis_status()}
    return JSONResponse(status_code=status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)


@router.get("/metrics")
async def metrics_endpoint():
    return await prometheus_metrics()


@router.get("/configs", response_model=list[ConfigSummary])
async def list_configs(request: Request, _: Actor = Depends(get_actor)):
    return container_from_request(request).config_service.list_configs()


@router.post("/configs", response_model=ConfigVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_config(
    payload: ConfigCreateRequest,
    request: Request,
    actor: Actor = Depends(require_role("admin", "operator")),
):
    return await container_from_request(request).config_service.create_config(payload, actor)


@router.get("/configs/{name}", response_model=ConfigReadResponse)
async def get_config(
    name: str,
    request: Request,
    version: str | None = Query(default="resolved"),
    target: str | None = Query(default=None),
    client_id: str | None = Query(default=None),
    _: Actor = Depends(get_actor),
):
    return container_from_request(request).config_service.get_config(
        name=name,
        version=version,
        target=target,
        client_id=client_id,
    )


@router.get("/configs/{name}/versions", response_model=list[VersionHistoryEntry])
async def get_versions(name: str, request: Request, _: Actor = Depends(get_actor)):
    return container_from_request(request).config_service.list_versions(name)


@router.post("/configs/{name}/rollout", response_model=RolloutResponse)
async def rollout_config(
    name: str,
    payload: RolloutRequest,
    request: Request,
    actor: Actor = Depends(require_role("admin", "operator")),
):
    return await container_from_request(request).config_service.start_rollout(name, payload, actor)


@router.post("/configs/{name}/rollouts/{rollout_id}/promote", response_model=RolloutResponse)
async def promote_rollout(
    name: str,
    rollout_id: str,
    request: Request,
    actor: Actor = Depends(require_role("admin", "operator")),
):
    return await container_from_request(request).config_service.manual_promote_rollout(name, rollout_id, actor)


@router.post("/configs/{name}/rollback", response_model=RolloutResponse)
async def rollback_config(
    name: str,
    payload: RollbackRequest,
    request: Request,
    actor: Actor = Depends(require_role("admin", "operator")),
):
    return await container_from_request(request).config_service.rollback(name, payload, actor)


@router.post("/configs/{name}/schema/dry-run", response_model=DryRunMigrationResponse)
async def dry_run_schema_migration(
    name: str,
    payload: DryRunMigrationRequest,
    request: Request,
    _: Actor = Depends(require_role("admin", "operator")),
):
    return container_from_request(request).config_service.dry_run_migration(name, payload)


@router.get("/audit", response_model=list[AuditEntryResponse])
async def get_audit(
    request: Request,
    name: str | None = Query(default=None),
    _: Actor = Depends(require_role("admin", "operator", "reader")),
):
    return container_from_request(request).config_service.list_audit_logs(name)


@router.post("/simulation/metrics", response_model=SimulationMetricResponse)
async def set_simulation_metric(
    payload: SimulationMetricUpdate,
    request: Request,
    _: Actor = Depends(require_role("admin", "operator")),
):
    return container_from_request(request).config_service.set_metric(payload)


@router.post("/telemetry/failures", response_model=FailureTelemetryResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_failure_telemetry(
    payload: FailureTelemetryRequest,
    request: Request,
    _: Actor = Depends(get_actor),
):
    return container_from_request(request).telemetry_service.ingest_failure(payload)


@router.get("/telemetry/failures", response_model=list[FailureTelemetryEventResponse])
async def list_failure_telemetry(
    request: Request,
    config_name: str | None = Query(default=None),
    target: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: Actor = Depends(require_role("admin", "operator")),
):
    return container_from_request(request).telemetry_service.list_failures(
        config_name=config_name,
        target=target,
        source=source,
        limit=limit,
    )


@router.get("/telemetry/failures/summary", response_model=list[FailureTelemetrySummaryResponse])
async def summarize_failure_telemetry(
    request: Request,
    config_name: str | None = Query(default=None),
    target: str | None = Query(default=None),
    window_minutes: int = Query(default=60, ge=1, le=10080),
    limit: int = Query(default=50, ge=1, le=200),
    _: Actor = Depends(require_role("admin", "operator")),
):
    return container_from_request(request).telemetry_service.summarize_failures(
        config_name=config_name,
        target=target,
        window_minutes=window_minutes,
        limit=limit,
    )


@router.get("/watch/longpoll", response_model=NotificationEvent | None)
async def longpoll(
    request: Request,
    last_sequence: int = Query(default=0, ge=0),
    config_name: str | None = Query(default=None),
    target: str | None = Query(default=None),
    timeout: float | None = Query(default=None, gt=0),
    actor: Actor = Depends(get_actor),
):
    if actor.role == "reader" and not (config_name or target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="reader long-poll subscriptions must scope by config_name or target",
        )
    container = container_from_request(request)
    event = await container.notifications.poll(
        last_sequence=last_sequence,
        config_name=config_name,
        target=target,
        timeout=timeout or container.settings.longpoll_timeout_seconds,
    )
    if event is None:
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
    return event


@router.websocket("/watch/ws")
async def websocket_watch(websocket: WebSocket):
    container = container_from_ws(websocket)
    actor = get_websocket_actor(websocket)
    config_name = websocket.query_params.get("config_name")
    target = websocket.query_params.get("target")
    if actor.role == "reader" and not (config_name or target):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="reader websocket subscriptions must scope by config_name or target",
        )
    await container.notifications.register(websocket, config_name=config_name, target=target)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await container.notifications.unregister(websocket)
    except Exception:
        await container.notifications.unregister(websocket)
        await websocket.close()
