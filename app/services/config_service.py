from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from jsonschema.exceptions import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from app.core.metrics import CONFIG_MUTATIONS, ROLLOUT_EVENTS
from app.core.security import Actor
from app.core.settings import Settings
from app.db.models import AuditLog, ConfigAssignment, ConfigVersion, Rollout
from app.db.session import Database
from app.schemas.audit import AuditEntryResponse
from app.schemas.config import (
    ConfigCreateRequest,
    ConfigReadResponse,
    ConfigSummary,
    ConfigVersionResponse,
    DryRunMigrationRequest,
    DryRunMigrationResponse,
    RollbackRequest,
    RolloutRequest,
    RolloutResponse,
    SimulationMetricResponse,
    SimulationMetricUpdate,
    VersionHistoryEntry,
)
from app.services.cache import CacheService
from app.services.notifications import NotificationHub
from app.services.validation import validate_payload, validate_schema

logger = logging.getLogger(__name__)


SYSTEM_ACTOR = Actor(user_id="system:canary", role="admin")


class ConfigService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        cache: CacheService,
        notifications: NotificationHub,
    ) -> None:
        self.settings = settings
        self.database = database
        self.cache = cache
        self.notifications = notifications

    async def create_config(self, payload: ConfigCreateRequest, actor: Actor) -> ConfigVersionResponse:
        warnings: list[str] = []
        for _ in range(3):
            with self.database.session() as session:
                latest = self._get_latest_version(session, payload.name)
                schema = payload.schema_ or (latest.schema if latest else None)
                if schema is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="schema is required for the first config version",
                    )
                try:
                    validate_schema(schema)
                    issues = validate_payload(payload.value, schema)
                except Exception as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                if issues:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=issues)
                if latest and payload.schema_ and payload.schema_ != latest.schema:
                    compatibility = self._compatibility_report(session, payload.name, payload.schema_)
                    if compatibility["incompatible_versions"]:
                        warnings.append(
                            "new schema is incompatible with prior versions: "
                            + ", ".join(str(v) for v in compatibility["incompatible_versions"])
                        )
                next_version = (latest.version + 1) if latest else 1
                if latest:
                    latest.is_latest = False
                record = ConfigVersion(
                    name=payload.name,
                    version=next_version,
                    value=payload.value,
                    schema=schema,
                    description=payload.description,
                    created_by=actor.user_id,
                    is_latest=True,
                )
                session.add(record)
                activation_target = None
                activated = False
                try:
                    if next_version == 1:
                        activation_target = self.infer_target(payload.name)
                        session.add(
                            ConfigAssignment(
                                config_name=payload.name,
                                target=activation_target,
                                stable_version=next_version,
                            )
                        )
                        activated = True
                    self._create_audit(
                        session,
                        actor=actor,
                        action="config.create",
                        config_name=payload.name,
                        version=next_version,
                        details={
                            "description": payload.description,
                            "activated": activated,
                            "schema_changed": payload.schema_ is not None and bool(latest),
                        },
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                self._cache_version(record)
                CONFIG_MUTATIONS.labels("create").inc()
                if activated and activation_target:
                    await self._publish_event(
                        event="activated",
                        config_name=payload.name,
                        target=activation_target,
                        version=next_version,
                        stable_version=next_version,
                        reason="initial activation",
                    )
                return ConfigVersionResponse(
                    config_id=record.config_id,
                    name=record.name,
                    version=record.version,
                    description=record.description,
                    created_by=record.created_by,
                    created_at=record.created_at,
                    active_target=activation_target,
                    activated=activated,
                    warnings=warnings,
                )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="concurrent version creation conflict")

    def list_configs(self) -> list[ConfigSummary]:
        with self.database.session() as session:
            latest_versions = (
                session.execute(select(ConfigVersion).where(ConfigVersion.is_latest.is_(True)).order_by(ConfigVersion.name))
                .scalars()
                .all()
            )
            assignments = session.execute(select(ConfigAssignment).order_by(ConfigAssignment.config_name)).scalars().all()
            assignment_map = {(item.config_name, item.target): item for item in assignments}
            results: list[ConfigSummary] = []
            for version in latest_versions:
                preferred_target = self.infer_target(version.name)
                assignment = assignment_map.get((version.name, preferred_target))
                if assignment is None:
                    assignment = next((item for item in assignments if item.config_name == version.name), None)
                stable_version = assignment.stable_version if assignment else version.version
                stable_target = assignment.target if assignment else preferred_target
                results.append(
                    ConfigSummary(
                        name=version.name,
                        latest_version=version.version,
                        stable_target=stable_target,
                        stable_version=stable_version,
                        updated_at=version.created_at,
                    )
                )
            return results

    def list_versions(self, name: str) -> list[VersionHistoryEntry]:
        with self.database.session() as session:
            versions = (
                session.execute(select(ConfigVersion).where(ConfigVersion.name == name).order_by(desc(ConfigVersion.version)))
                .scalars()
                .all()
            )
            if not versions:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
            return [
                VersionHistoryEntry(
                    version=item.version,
                    created_at=item.created_at,
                    created_by=item.created_by,
                    description=item.description,
                    is_latest=item.is_latest,
                )
                for item in versions
            ]

    def get_config(
        self,
        *,
        name: str,
        version: str | int | None,
        target: str | None,
        client_id: str | None,
    ) -> ConfigReadResponse:
        resolved_target = target or self.infer_target(name)
        with self.database.session() as session:
            source = "stable"
            if version is None or version == "resolved":
                resolved_version = self._resolve_stable_version(session, name, resolved_target)
                active_rollout = self._get_active_rollout(session, name, resolved_target)
                if active_rollout and client_id and self._is_canary_client(name, resolved_target, client_id, active_rollout.percent):
                    resolved_version = active_rollout.to_version
                    source = "canary"
                record = self._get_version(session, name, resolved_version)
            elif version == "latest":
                record = self._get_latest_version(session, name)
                if record is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
                source = "latest"
            else:
                try:
                    version_int = int(version)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="version must be 'resolved', 'latest', or an integer",
                    ) from exc
                record = self._get_version(session, name, version_int)
                source = "explicit"
            return ConfigReadResponse(
                name=record.name,
                version=record.version,
                target=resolved_target,
                source=source,
                description=record.description,
                value=record.value,
                schema_=record.schema,
                created_at=record.created_at,
            )

    async def start_rollout(self, name: str, payload: RolloutRequest, actor: Actor) -> RolloutResponse:
        with self.database.session() as session:
            latest = self._get_latest_version(session, name)
            if latest is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
            assignment = self._get_or_create_assignment(session, name, payload.target)
            if assignment.stable_version == latest.version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="latest version is already stable; create a new version before starting a rollout",
                )
            existing = self._get_active_rollout(session, name, payload.target)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"an active rollout already exists for {name}/{payload.target}",
                )
            if payload.percent == 100 and payload.canary_check is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="100% rollout cannot include canary_check; use a partial rollout and promote later",
                )
            rollout_status = "promoted" if payload.percent == 100 else "active"
            rollout = Rollout(
                config_name=name,
                target=payload.target,
                from_version=assignment.stable_version,
                to_version=latest.version,
                percent=payload.percent,
                status=rollout_status,
                canary_metric=payload.canary_check.metric if payload.canary_check else None,
                canary_threshold=payload.canary_check.threshold if payload.canary_check else None,
                canary_window_minutes=payload.canary_check.window if payload.canary_check else None,
                created_by=actor.user_id,
            )
            session.add(rollout)
            if rollout.status == "promoted":
                assignment.stable_version = rollout.to_version
            self._create_audit(
                session,
                actor=actor,
                action="config.rollout",
                config_name=name,
                version=rollout.to_version,
                details={
                    "target": payload.target,
                    "percent": payload.percent,
                    "from_version": rollout.from_version,
                    "to_version": rollout.to_version,
                    "canary_check": payload.canary_check.model_dump() if payload.canary_check else None,
                    "status": rollout.status,
                },
            )
            session.commit()
            ROLLOUT_EVENTS.labels(rollout.status).inc()
        if rollout.status == "promoted":
            await self._publish_event(
                event="rollout_promoted",
                config_name=name,
                target=payload.target,
                version=rollout.to_version,
                stable_version=rollout.to_version,
                rollout_percent=100,
                rollout_id=rollout.rollout_id,
                reason="promoted immediately at 100%",
            )
        else:
            await self._publish_event(
                event="rollout_started",
                config_name=name,
                target=payload.target,
                version=rollout.to_version,
                stable_version=rollout.from_version,
                rollout_percent=payload.percent,
                rollout_id=rollout.rollout_id,
            )
        return RolloutResponse.model_validate(rollout, from_attributes=True)

    async def rollback(self, name: str, payload: RollbackRequest, actor: Actor, reason: str = "manual rollback") -> RolloutResponse:
        target = payload.target or self.infer_target(name)
        with self.database.session() as session:
            version = self._get_version(session, name, payload.target_version)
            assignment = self._get_or_create_assignment(session, name, target)
            previous_stable = assignment.stable_version
            active_rollout = self._get_active_rollout(session, name, target)
            if active_rollout:
                active_rollout.status = "rolled_back"
                active_rollout.rollback_reason = reason
            assignment.stable_version = version.version
            rollback_view = active_rollout or Rollout(
                config_name=name,
                target=target,
                from_version=previous_stable,
                to_version=version.version,
                percent=0,
                status="rolled_back",
                created_by=actor.user_id,
                rollback_reason=reason,
            )
            self._create_audit(
                session,
                actor=actor,
                action="config.rollback",
                config_name=name,
                version=version.version,
                details={"target": target, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("rolled_back").inc()
        await self._publish_event(
            event="rollback",
            config_name=name,
            target=target,
            version=version.version,
            stable_version=version.version,
            rollout_percent=0,
            rollout_id=getattr(active_rollout, "rollout_id", None),
            reason=reason,
        )
        return RolloutResponse(
            rollout_id=getattr(rollback_view, "rollout_id", f"manual-{name}-{target}-{version.version}"),
            config_name=name,
            target=target,
            from_version=getattr(rollback_view, "from_version", version.version),
            to_version=version.version,
            percent=0,
            status="rolled_back",
            created_at=getattr(rollback_view, "created_at", datetime.now(timezone.utc)),
            rollback_reason=reason,
        )

    async def manual_promote_rollout(self, name: str, rollout_id: str, actor: Actor) -> RolloutResponse:
        response = await self._promote_rollout(
            rollout_id,
            actor=actor,
            action="config.rollout.promote",
            reason="manual promotion",
            expected_config_name=name,
            strict=True,
        )
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"rollout '{rollout_id}' not found")
        return response

    async def promote_rollout(self, rollout_id: str) -> None:
        await self._promote_rollout(
            rollout_id,
            actor=SYSTEM_ACTOR,
            action="config.rollout.auto_promote",
            reason="healthy canary window elapsed",
            strict=False,
        )

    async def auto_rollback_rollout(self, rollout_id: str, reason: str) -> None:
        with self.database.session() as session:
            rollout = session.get(Rollout, rollout_id)
            if rollout is None or rollout.status != "active":
                return
            assignment = self._get_or_create_assignment(session, rollout.config_name, rollout.target)
            assignment.stable_version = rollout.from_version
            rollout.status = "rolled_back"
            rollout.rollback_reason = reason
            self._create_audit(
                session,
                actor=SYSTEM_ACTOR,
                action="config.rollout.auto_rollback",
                config_name=rollout.config_name,
                version=rollout.from_version,
                details={"target": rollout.target, "rollout_id": rollout_id, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("rolled_back").inc()
            event = {
                "config_name": rollout.config_name,
                "target": rollout.target,
                "version": rollout.from_version,
                "stable_version": rollout.from_version,
                "rollout_percent": 0,
                "rollout_id": rollout.rollout_id,
            }
        await self._publish_event(event="rollback", **event, reason=reason)

    def get_active_rollouts(self) -> list[Rollout]:
        with self.database.session() as session:
            return session.execute(select(Rollout).where(Rollout.status == "active")).scalars().all()

    def get_metric_value(self, target: str, metric: str) -> float | None:
        record = self.cache.get_metric(target, metric)
        if record is None:
            return None
        return float(record["value"])

    def set_metric(self, payload: SimulationMetricUpdate) -> SimulationMetricResponse:
        record = self.cache.set_metric(payload.target, payload.metric, payload.value)
        timestamp = datetime.fromisoformat(record["timestamp"])
        return SimulationMetricResponse(
            target=payload.target,
            metric=payload.metric,
            value=payload.value,
            timestamp=timestamp,
        )

    def dry_run_migration(self, name: str, payload: DryRunMigrationRequest) -> DryRunMigrationResponse:
        try:
            validate_schema(payload.schema_)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        with self.database.session() as session:
            versions = (
                session.execute(select(ConfigVersion).where(ConfigVersion.name == name).order_by(ConfigVersion.version))
                .scalars()
                .all()
            )
            if not versions:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
            compatible: list[int] = []
            incompatible: list[int] = []
            issues: list[str] = []
            for item in versions:
                errors = validate_payload(item.value, payload.schema_)
                if errors:
                    incompatible.append(item.version)
                    issues.append(f"version {item.version}: {'; '.join(errors)}")
                else:
                    compatible.append(item.version)
            candidate_errors = validate_payload(payload.value or versions[-1].value, payload.schema_)
            return DryRunMigrationResponse(
                config_name=name,
                candidate_value_valid=not candidate_errors,
                current_versions_checked=len(versions),
                compatible_versions=compatible,
                incompatible_versions=incompatible,
                issues=issues + ([f"candidate value: {'; '.join(candidate_errors)}"] if candidate_errors else []),
            )

    def list_audit_logs(self, name: str | None = None) -> list[AuditEntryResponse]:
        with self.database.session() as session:
            stmt = select(AuditLog).order_by(desc(AuditLog.timestamp))
            if name:
                stmt = stmt.where(AuditLog.config_name == name)
            records = session.execute(stmt).scalars().all()
            return [AuditEntryResponse.model_validate(record, from_attributes=True) for record in records]

    def redis_status(self) -> bool:
        return self.cache.is_available()

    def infer_target(self, config_name: str) -> str:
        if "." in config_name:
            return config_name.split(".", 1)[0]
        return self.settings.default_target

    def _get_latest_version(self, session, name: str) -> ConfigVersion | None:
        return session.execute(
            select(ConfigVersion).where(ConfigVersion.name == name).order_by(desc(ConfigVersion.version)).limit(1)
        ).scalar_one_or_none()

    def _get_version(self, session, name: str, version: int) -> ConfigVersion:
        record = session.execute(
            select(ConfigVersion).where(ConfigVersion.name == name, ConfigVersion.version == version).limit(1)
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"config '{name}' version {version} not found",
            )
        return record

    def _get_or_create_assignment(self, session, name: str, target: str) -> ConfigAssignment:
        assignment = self._get_assignment(session, name, target)
        if assignment:
            return assignment
        latest = self._get_latest_version(session, name)
        if latest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
        default_target = self.infer_target(name)
        source_assignment = session.execute(
            select(ConfigAssignment).where(
                ConfigAssignment.config_name == name,
                ConfigAssignment.target == default_target,
            )
        ).scalar_one_or_none()
        stable_version = source_assignment.stable_version if source_assignment else latest.version
        assignment = ConfigAssignment(config_name=name, target=target, stable_version=stable_version)
        session.add(assignment)
        session.flush()
        return assignment

    def _get_assignment(self, session, name: str, target: str) -> ConfigAssignment | None:
        return session.execute(
            select(ConfigAssignment).where(ConfigAssignment.config_name == name, ConfigAssignment.target == target).limit(1)
        ).scalar_one_or_none()

    def _resolve_stable_version(self, session, name: str, target: str) -> int:
        assignment = self._get_assignment(session, name, target)
        if assignment:
            return assignment.stable_version
        default_target = self.infer_target(name)
        if target != default_target:
            fallback_assignment = self._get_assignment(session, name, default_target)
            if fallback_assignment:
                return fallback_assignment.stable_version
        latest = self._get_latest_version(session, name)
        if latest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"config '{name}' not found")
        return latest.version

    def _get_active_rollout(self, session, name: str, target: str) -> Rollout | None:
        return session.execute(
            select(Rollout)
            .where(Rollout.config_name == name, Rollout.target == target, Rollout.status == "active")
            .order_by(desc(Rollout.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _is_canary_client(self, name: str, target: str, client_id: str, percent: int) -> bool:
        bucket = int(hashlib.sha256(f"{name}:{target}:{client_id}".encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < percent

    def _cache_version(self, version: ConfigVersion) -> None:
        payload = {
            "name": version.name,
            "version": version.version,
            "value": version.value,
            "schema": version.schema,
            "description": version.description,
            "created_at": version.created_at.isoformat(),
        }
        self.cache.set_json(f"config:{version.name}:latest", payload)
        self.cache.set_json(f"config:{version.name}:version:{version.version}", payload)

    async def _publish_event(
        self,
        *,
        event: str,
        config_name: str,
        target: str,
        version: int,
        stable_version: int,
        rollout_percent: int | None = None,
        rollout_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        payload = {
            "event": event,
            "config_name": config_name,
            "target": target,
            "version": version,
            "stable_version": stable_version,
            "rollout_percent": rollout_percent,
            "rollout_id": rollout_id,
            "reason": reason,
            "source_instance": self.settings.instance_id,
        }
        await self.notifications.publish(payload)
        self.cache.publish(self.settings.notification_channel, payload)

    async def _promote_rollout(
        self,
        rollout_id: str,
        *,
        actor: Actor,
        action: str,
        reason: str,
        expected_config_name: str | None = None,
        strict: bool,
    ) -> RolloutResponse | None:
        with self.database.session() as session:
            rollout = session.get(Rollout, rollout_id)
            if rollout is None or (expected_config_name and rollout.config_name != expected_config_name):
                if strict:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"rollout '{rollout_id}' not found")
                return None
            if rollout.status != "active":
                if strict:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"rollout '{rollout_id}' is not active",
                    )
                return None
            assignment = self._get_or_create_assignment(session, rollout.config_name, rollout.target)
            assignment.stable_version = rollout.to_version
            rollout.status = "promoted"
            rollout.rollback_reason = None
            self._create_audit(
                session,
                actor=actor,
                action=action,
                config_name=rollout.config_name,
                version=rollout.to_version,
                details={"target": rollout.target, "rollout_id": rollout_id, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("promoted").inc()
            response = RolloutResponse.model_validate(rollout, from_attributes=True)
            event = {
                "config_name": rollout.config_name,
                "target": rollout.target,
                "version": rollout.to_version,
                "stable_version": rollout.to_version,
                "rollout_percent": 100,
                "rollout_id": rollout.rollout_id,
            }
        await self._publish_event(event="rollout_promoted", **event, reason=reason)
        return response

    def _create_audit(
        self,
        session,
        *,
        actor: Actor,
        action: str,
        config_name: str,
        version: int | None,
        details: dict[str, Any],
    ) -> None:
        session.add(
            AuditLog(
                user_id=actor.user_id,
                action=action,
                config_name=config_name,
                version=version,
                details=details,
            )
        )

    def _compatibility_report(self, session, name: str, schema: dict[str, Any]) -> dict[str, Sequence[int]]:
        versions = session.execute(select(ConfigVersion).where(ConfigVersion.name == name)).scalars().all()
        compatible: list[int] = []
        incompatible: list[int] = []
        for version in versions:
            if validate_payload(version.value, schema):
                incompatible.append(version.version)
            else:
                compatible.append(version.version)
        return {"compatible_versions": compatible, "incompatible_versions": incompatible}
