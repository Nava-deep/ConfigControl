from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select

from app.core.metrics import CLIENT_FAILURE_REPORTS
from app.core.settings import Settings
from app.db.models import ClientFailureEvent
from app.schemas.config import EnvironmentName
from app.db.session import Database
from app.schemas.telemetry import (
    FailureTelemetryEventResponse,
    FailureTelemetryRequest,
    FailureTelemetryResponse,
    FailureTelemetrySummaryResponse,
)

BLOCKED_METADATA_SUBSTRINGS = {
    "message",
    "stack",
    "trace",
    "email",
    "token",
    "secret",
    "username",
    "user",
    "name",
}


class TelemetryService:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def ingest_failure(self, payload: FailureTelemetryRequest) -> FailureTelemetryResponse:
        installation_hash = self._anonymize_installation_id(payload.anonymous_installation_id)
        metadata = self._sanitize_metadata(payload.metadata)
        with self.database.session() as session:
            event = ClientFailureEvent(
                config_name=payload.config_name,
                environment=payload.environment,
                target=payload.target,
                source=payload.source,
                error_type=payload.error_type,
                fingerprint=payload.fingerprint,
                anonymous_installation_hash=installation_hash,
                config_version=payload.config_version,
                config_source=payload.config_source,
                sdk_version=payload.sdk_version,
                app_version=payload.app_version,
                runtime=payload.runtime,
                attributes=metadata,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
        CLIENT_FAILURE_REPORTS.labels(payload.target, payload.source, payload.environment).inc()
        return FailureTelemetryResponse(
            event_id=event.event_id,
            ingested_at=event.occurred_at,
            fingerprint=event.fingerprint,
            anonymous_installation_hash=installation_hash,
        )

    def list_failures(
        self,
        *,
        config_name: str | None = None,
        environment: EnvironmentName | None = None,
        target: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[FailureTelemetryEventResponse]:
        with self.database.session() as session:
            stmt = select(ClientFailureEvent).order_by(desc(ClientFailureEvent.occurred_at)).limit(limit)
            if config_name:
                stmt = stmt.where(ClientFailureEvent.config_name == config_name)
            if environment:
                stmt = stmt.where(ClientFailureEvent.environment == environment)
            if target:
                stmt = stmt.where(ClientFailureEvent.target == target)
            if source:
                stmt = stmt.where(ClientFailureEvent.source == source)
            events = session.execute(stmt).scalars().all()
            return [
                FailureTelemetryEventResponse(
                    event_id=event.event_id,
                    config_name=event.config_name,
                    environment=event.environment,
                    target=event.target,
                    source=event.source,
                    error_type=event.error_type,
                    fingerprint=event.fingerprint,
                    anonymous_installation_hash=event.anonymous_installation_hash,
                    config_version=event.config_version,
                    config_source=event.config_source,
                    sdk_version=event.sdk_version,
                    app_version=event.app_version,
                    runtime=event.runtime,
                    metadata=event.attributes,
                    occurred_at=self._ensure_utc(event.occurred_at),
                )
                for event in events
            ]

    def summarize_failures(
        self,
        *,
        config_name: str | None = None,
        environment: EnvironmentName | None = None,
        target: str | None = None,
        window_minutes: int = 60,
        limit: int = 50,
    ) -> list[FailureTelemetrySummaryResponse]:
        if window_minutes < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="window_minutes must be >= 1",
            )
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        with self.database.session() as session:
            stmt = (
                select(
                    ClientFailureEvent.config_name.label("config_name"),
                    ClientFailureEvent.environment.label("environment"),
                    ClientFailureEvent.target.label("target"),
                    ClientFailureEvent.error_type.label("error_type"),
                    ClientFailureEvent.fingerprint.label("fingerprint"),
                    func.count(ClientFailureEvent.event_id).label("event_count"),
                    func.count(func.distinct(ClientFailureEvent.anonymous_installation_hash)).label("distinct_installations"),
                    func.max(ClientFailureEvent.occurred_at).label("last_seen"),
                    func.max(ClientFailureEvent.config_version).label("latest_config_version"),
                )
                .where(ClientFailureEvent.occurred_at >= since)
                .group_by(
                    ClientFailureEvent.config_name,
                    ClientFailureEvent.environment,
                    ClientFailureEvent.target,
                    ClientFailureEvent.error_type,
                    ClientFailureEvent.fingerprint,
                )
                .order_by(desc("event_count"), desc("last_seen"))
                .limit(limit)
            )
            if config_name:
                stmt = stmt.where(ClientFailureEvent.config_name == config_name)
            if environment:
                stmt = stmt.where(ClientFailureEvent.environment == environment)
            if target:
                stmt = stmt.where(ClientFailureEvent.target == target)
            rows = session.execute(stmt).all()
        return [
            FailureTelemetrySummaryResponse(
                config_name=row.config_name,
                environment=row.environment,
                target=row.target,
                error_type=row.error_type,
                fingerprint=row.fingerprint,
                event_count=row.event_count,
                distinct_installations=row.distinct_installations,
                last_seen=self._ensure_utc(row.last_seen),
                latest_config_version=row.latest_config_version,
            )
            for row in rows
        ]

    def _anonymize_installation_id(self, installation_id: str) -> str:
        return hashlib.sha256(f"{self.settings.telemetry_hash_salt}:{installation_id}".encode("utf-8")).hexdigest()

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in list(metadata.items())[:12]:
            normalized_key = key.strip().lower()
            if not normalized_key:
                continue
            if any(item in normalized_key for item in BLOCKED_METADATA_SUBSTRINGS):
                continue
            if isinstance(value, bool | int | float) or value is None:
                sanitized[normalized_key] = value
            elif isinstance(value, str):
                sanitized[normalized_key] = value[:120]
        return sanitized

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
