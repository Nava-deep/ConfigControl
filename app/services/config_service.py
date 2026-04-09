from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from app.core.metrics import CONFIG_FETCHES, CONFIG_MUTATIONS, ROLLOUT_EVENTS, VALIDATION_FAILURES
from app.core.security import Actor
from app.core.settings import Settings
from app.db.models import AuditLog, ConfigAssignment, ConfigVersion, Rollout
from app.db.session import Database
from app.schemas.audit import AuditEntryResponse
from app.schemas.config import (
    ConfigCreateRequest,
    ConfigDiffEntry,
    ConfigDiffResponse,
    ConfigReadResponse,
    ConfigSummary,
    ConfigVersionResponse,
    DryRunMigrationRequest,
    DryRunMigrationResponse,
    EnvironmentName,
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
                latest = self._get_latest_version(session, payload.name, payload.environment)
                schema = payload.schema_ or (latest.schema if latest else None)
                if schema is None:
                    VALIDATION_FAILURES.labels("create", payload.environment).inc()
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="schema is required for the first config version",
                    )
                try:
                    validate_schema(schema)
                    issues = validate_payload(payload.value, schema)
                except Exception as exc:
                    VALIDATION_FAILURES.labels("create", payload.environment).inc()
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                if issues:
                    VALIDATION_FAILURES.labels("create", payload.environment).inc()
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=issues)
                if latest and payload.schema_ and payload.schema_ != latest.schema:
                    compatibility = self._compatibility_report(session, payload.name, payload.schema_, payload.environment)
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
                    environment=payload.environment,
                    version=next_version,
                    value=payload.value,
                    schema=schema,
                    labels=payload.labels,
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
                                environment=payload.environment,
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
                        environment=payload.environment,
                        version=next_version,
                        details={
                            "description": payload.description,
                            "labels": payload.labels,
                            "activated": activated,
                            "schema_changed": payload.schema_ is not None and bool(latest),
                        },
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                self._cache_version(record)
                CONFIG_MUTATIONS.labels("create", payload.environment).inc()
                logger.info(
                    "config version created",
                    extra={
                        "event": "config.create",
                        "context": {
                            "config_name": payload.name,
                            "environment": payload.environment,
                            "version": next_version,
                            "actor": actor.user_id,
                            "activated": activated,
                        },
                    },
                )
                if activated and activation_target:
                    await self._publish_event(
                        event="activated",
                        config_name=payload.name,
                        environment=payload.environment,
                        target=activation_target,
                        version=next_version,
                        stable_version=next_version,
                        reason="initial activation",
                    )
                return ConfigVersionResponse(
                    config_id=record.config_id,
                    name=record.name,
                    environment=record.environment,
                    version=record.version,
                    labels=record.labels,
                    description=record.description,
                    created_by=record.created_by,
                    created_at=record.created_at,
                    active_target=activation_target,
                    activated=activated,
                    warnings=warnings,
                )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="concurrent version creation conflict")

    def list_configs(self, environment: EnvironmentName | None = None) -> list[ConfigSummary]:
        with self.database.session() as session:
            stmt = select(ConfigVersion).where(ConfigVersion.is_latest.is_(True)).order_by(
                ConfigVersion.name, ConfigVersion.environment
            )
            if environment:
                stmt = stmt.where(ConfigVersion.environment == environment)
            latest_versions = session.execute(stmt).scalars().all()

            assignment_stmt = select(ConfigAssignment).order_by(
                ConfigAssignment.config_name,
                ConfigAssignment.environment,
            )
            if environment:
                assignment_stmt = assignment_stmt.where(ConfigAssignment.environment == environment)
            assignments = session.execute(assignment_stmt).scalars().all()

            assignment_map = {(item.config_name, item.environment, item.target): item for item in assignments}
            results: list[ConfigSummary] = []
            for version in latest_versions:
                preferred_target = self.infer_target(version.name)
                assignment = assignment_map.get((version.name, version.environment, preferred_target))
                if assignment is None:
                    assignment = next(
                        (
                            item
                            for item in assignments
                            if item.config_name == version.name and item.environment == version.environment
                        ),
                        None,
                    )
                stable_version = assignment.stable_version if assignment else version.version
                stable_target = assignment.target if assignment else preferred_target
                results.append(
                    ConfigSummary(
                        name=version.name,
                        environment=version.environment,
                        latest_version=version.version,
                        stable_target=stable_target,
                        stable_version=stable_version,
                        updated_at=version.created_at,
                    )
                )
            return results

    def list_versions(self, name: str, environment: EnvironmentName = "prod") -> list[VersionHistoryEntry]:
        with self.database.session() as session:
            versions = (
                session.execute(
                    select(ConfigVersion)
                    .where(ConfigVersion.name == name, ConfigVersion.environment == environment)
                    .order_by(desc(ConfigVersion.version))
                )
                .scalars()
                .all()
            )
            if not versions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"config '{name}' not found in environment '{environment}'",
                )
            return [
                VersionHistoryEntry(
                    environment=item.environment,
                    version=item.version,
                    created_at=item.created_at,
                    created_by=item.created_by,
                    labels=item.labels,
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
        environment: EnvironmentName,
    ) -> ConfigReadResponse:
        resolved_target = target or self.infer_target(name)
        source = "stable"
        try:
            if version is None or version == "resolved":
                with self.database.session() as session:
                    resolved_version = self._resolve_stable_version(session, name, resolved_target, environment)
                    active_rollout = self._get_active_rollout(session, name, resolved_target, environment)
                    if active_rollout and client_id and self._is_canary_client(
                        name,
                        environment,
                        resolved_target,
                        client_id,
                        active_rollout.percent,
                    ):
                        resolved_version = active_rollout.to_version
                        source = "canary"
                    cached_payload = self._get_cached_version_payload(name, environment, resolved_version)
                    if cached_payload is not None:
                        CONFIG_FETCHES.labels(source, environment, "success").inc()
                        return self._payload_to_read_response(cached_payload, target=resolved_target, source=source)
                    record = self._get_version(session, name, environment, resolved_version)
            elif version == "latest":
                source = "latest"
                cached_payload = self.cache.get_json(self._latest_cache_key(name, environment))
                if cached_payload is not None:
                    CONFIG_FETCHES.labels(source, environment, "success").inc()
                    return self._payload_to_read_response(cached_payload, target=resolved_target, source=source)
                with self.database.session() as session:
                    record = self._get_latest_version(session, name, environment)
                    if record is None:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"config '{name}' not found in environment '{environment}'",
                        )
                    self._cache_version(record)
            else:
                source = "explicit"
                try:
                    version_int = int(version)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="version must be 'resolved', 'latest', or an integer",
                    ) from exc
                cached_payload = self._get_cached_version_payload(name, environment, version_int)
                if cached_payload is not None:
                    CONFIG_FETCHES.labels(source, environment, "success").inc()
                    return self._payload_to_read_response(cached_payload, target=resolved_target, source=source)
                with self.database.session() as session:
                    record = self._get_version(session, name, environment, version_int)
                    self._cache_version(record)
                response = ConfigReadResponse(
                    name=record.name,
                    environment=record.environment,
                    version=record.version,
                    target=resolved_target,
                    source=source,
                    description=record.description,
                    labels=record.labels,
                    value=record.value,
                    schema_=record.schema,
                    created_at=record.created_at,
                )
                CONFIG_FETCHES.labels(source, environment, "success").inc()
                return response
        except HTTPException:
            CONFIG_FETCHES.labels(source, environment, "error").inc()
            raise

    async def start_rollout(self, name: str, payload: RolloutRequest, actor: Actor) -> RolloutResponse:
        with self.database.session() as session:
            latest = self._get_latest_version(session, name, payload.environment)
            if latest is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"config '{name}' not found in environment '{payload.environment}'",
                )
            latest_issues = validate_payload(latest.value, latest.schema)
            if latest_issues:
                VALIDATION_FAILURES.labels("rollout", payload.environment).inc()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"latest version failed validation: {'; '.join(latest_issues)}",
                )
            assignment = self._get_or_create_assignment(session, name, payload.target, payload.environment)
            if assignment.stable_version == latest.version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="latest version is already stable; create a new version before starting a rollout",
                )
            existing = self._get_active_rollout(session, name, payload.target, payload.environment)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"an active rollout already exists for {name}/{payload.environment}/{payload.target}",
                )
            if payload.percent == 100 and payload.canary_check is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="100% rollout cannot include canary_check; use a partial rollout and promote later",
                )
            rollout_status = "promoted" if payload.percent == 100 else "active"
            rollout = Rollout(
                config_name=name,
                environment=payload.environment,
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
                environment=payload.environment,
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
            ROLLOUT_EVENTS.labels(rollout.status, payload.environment).inc()
            response = RolloutResponse.model_validate(rollout, from_attributes=True)
        logger.info(
            "rollout started",
            extra={
                "event": "config.rollout",
                "context": {
                    "config_name": name,
                    "environment": payload.environment,
                    "target": payload.target,
                    "rollout_id": rollout.rollout_id,
                    "from_version": rollout.from_version,
                    "to_version": rollout.to_version,
                    "status": rollout.status,
                    "percent": payload.percent,
                },
            },
        )
        if rollout.status == "promoted":
            await self._publish_event(
                event="rollout_promoted",
                config_name=name,
                environment=payload.environment,
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
                environment=payload.environment,
                target=payload.target,
                version=rollout.to_version,
                stable_version=rollout.from_version,
                rollout_percent=payload.percent,
                rollout_id=rollout.rollout_id,
            )
        return response

    async def rollback(
        self,
        name: str,
        payload: RollbackRequest,
        actor: Actor,
        reason: str = "manual rollback",
    ) -> RolloutResponse:
        target = payload.target or self.infer_target(name)
        with self.database.session() as session:
            version = self._get_version(session, name, payload.environment, payload.target_version)
            assignment = self._get_or_create_assignment(session, name, target, payload.environment)
            previous_stable = assignment.stable_version
            active_rollout = self._get_active_rollout(session, name, target, payload.environment)
            if active_rollout:
                active_rollout.status = "rolled_back"
                active_rollout.rollback_reason = reason
            assignment.stable_version = version.version
            rollback_view = active_rollout or Rollout(
                config_name=name,
                environment=payload.environment,
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
                environment=payload.environment,
                version=version.version,
                details={"target": target, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("rolled_back", payload.environment).inc()
        logger.info(
            "config rolled back",
            extra={
                "event": "config.rollback",
                "context": {
                    "config_name": name,
                    "environment": payload.environment,
                    "target": target,
                    "actor": actor.user_id,
                    "from_version": previous_stable,
                    "to_version": version.version,
                    "reason": reason,
                },
            },
        )
        await self._publish_event(
            event="rollback",
            config_name=name,
            environment=payload.environment,
            target=target,
            version=version.version,
            stable_version=version.version,
            rollout_percent=0,
            rollout_id=getattr(active_rollout, "rollout_id", None),
            reason=reason,
        )
        return RolloutResponse(
            rollout_id=getattr(rollback_view, "rollout_id", f"manual-{name}-{payload.environment}-{target}-{version.version}"),
            config_name=name,
            environment=payload.environment,
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
            assignment = self._get_or_create_assignment(session, rollout.config_name, rollout.target, rollout.environment)
            assignment.stable_version = rollout.from_version
            rollout.status = "rolled_back"
            rollout.rollback_reason = reason
            self._create_audit(
                session,
                actor=SYSTEM_ACTOR,
                action="config.rollout.auto_rollback",
                config_name=rollout.config_name,
                environment=rollout.environment,
                version=rollout.from_version,
                details={"target": rollout.target, "rollout_id": rollout_id, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("rolled_back", rollout.environment).inc()
            event = {
                "config_name": rollout.config_name,
                "environment": rollout.environment,
                "target": rollout.target,
                "version": rollout.from_version,
                "stable_version": rollout.from_version,
                "rollout_percent": 0,
                "rollout_id": rollout.rollout_id,
            }
        logger.warning(
            "rollout auto-rolled back",
            extra={
                "event": "config.rollout.auto_rollback",
                "context": {
                    "config_name": event["config_name"],
                    "environment": event["environment"],
                    "target": event["target"],
                    "rollout_id": event["rollout_id"],
                    "reason": reason,
                },
            },
        )
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
            VALIDATION_FAILURES.labels("dry_run", payload.environment).inc()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        with self.database.session() as session:
            versions = (
                session.execute(
                    select(ConfigVersion)
                    .where(ConfigVersion.name == name, ConfigVersion.environment == payload.environment)
                    .order_by(ConfigVersion.version)
                )
                .scalars()
                .all()
            )
            if not versions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"config '{name}' not found in environment '{payload.environment}'",
                )
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
            if issues or candidate_errors:
                VALIDATION_FAILURES.labels("dry_run", payload.environment).inc()
            return DryRunMigrationResponse(
                config_name=name,
                environment=payload.environment,
                candidate_value_valid=not candidate_errors,
                current_versions_checked=len(versions),
                compatible_versions=compatible,
                incompatible_versions=incompatible,
                issues=issues + ([f"candidate value: {'; '.join(candidate_errors)}"] if candidate_errors else []),
            )

    def list_audit_logs(self, name: str | None = None, environment: EnvironmentName | None = None) -> list[AuditEntryResponse]:
        with self.database.session() as session:
            stmt = select(AuditLog).order_by(desc(AuditLog.timestamp))
            if name:
                stmt = stmt.where(AuditLog.config_name == name)
            if environment:
                stmt = stmt.where(AuditLog.environment == environment)
            records = session.execute(stmt).scalars().all()
            return [AuditEntryResponse.model_validate(record, from_attributes=True) for record in records]

    def diff_versions(
        self,
        *,
        name: str,
        environment: EnvironmentName,
        from_version: int,
        to_version: int,
    ) -> ConfigDiffResponse:
        with self.database.session() as session:
            before = self._get_version(session, name, environment, from_version)
            after = self._get_version(session, name, environment, to_version)
        return ConfigDiffResponse(
            config_name=name,
            environment=environment,
            from_version=from_version,
            to_version=to_version,
            changes=self._diff_values(before.value, after.value),
        )

    def redis_status(self) -> bool:
        return self.cache.is_available()

    def infer_target(self, config_name: str) -> str:
        if "." in config_name:
            return config_name.split(".", 1)[0]
        return self.settings.default_target

    def _get_latest_version(self, session, name: str, environment: EnvironmentName) -> ConfigVersion | None:
        return session.execute(
            select(ConfigVersion)
            .where(ConfigVersion.name == name, ConfigVersion.environment == environment)
            .order_by(desc(ConfigVersion.version))
            .limit(1)
        ).scalar_one_or_none()

    def _get_version(self, session, name: str, environment: EnvironmentName, version: int) -> ConfigVersion:
        record = session.execute(
            select(ConfigVersion)
            .where(
                ConfigVersion.name == name,
                ConfigVersion.environment == environment,
                ConfigVersion.version == version,
            )
            .limit(1)
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"config '{name}' version {version} not found in environment '{environment}'",
            )
        return record

    def _get_or_create_assignment(self, session, name: str, target: str, environment: EnvironmentName) -> ConfigAssignment:
        assignment = self._get_assignment(session, name, target, environment)
        if assignment:
            return assignment
        latest = self._get_latest_version(session, name, environment)
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"config '{name}' not found in environment '{environment}'",
            )
        default_target = self.infer_target(name)
        source_assignment = session.execute(
            select(ConfigAssignment).where(
                ConfigAssignment.config_name == name,
                ConfigAssignment.environment == environment,
                ConfigAssignment.target == default_target,
            )
        ).scalar_one_or_none()
        stable_version = source_assignment.stable_version if source_assignment else latest.version
        assignment = ConfigAssignment(
            config_name=name,
            environment=environment,
            target=target,
            stable_version=stable_version,
        )
        session.add(assignment)
        session.flush()
        return assignment

    def _get_assignment(self, session, name: str, target: str, environment: EnvironmentName) -> ConfigAssignment | None:
        return session.execute(
            select(ConfigAssignment)
            .where(
                ConfigAssignment.config_name == name,
                ConfigAssignment.environment == environment,
                ConfigAssignment.target == target,
            )
            .limit(1)
        ).scalar_one_or_none()

    def _resolve_stable_version(self, session, name: str, target: str, environment: EnvironmentName) -> int:
        assignment = self._get_assignment(session, name, target, environment)
        if assignment:
            return assignment.stable_version
        default_target = self.infer_target(name)
        if target != default_target:
            fallback_assignment = self._get_assignment(session, name, default_target, environment)
            if fallback_assignment:
                return fallback_assignment.stable_version
        latest = self._get_latest_version(session, name, environment)
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"config '{name}' not found in environment '{environment}'",
            )
        return latest.version

    def _get_active_rollout(self, session, name: str, target: str, environment: EnvironmentName) -> Rollout | None:
        return session.execute(
            select(Rollout)
            .where(
                Rollout.config_name == name,
                Rollout.environment == environment,
                Rollout.target == target,
                Rollout.status == "active",
            )
            .order_by(desc(Rollout.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _is_canary_client(self, name: str, environment: EnvironmentName, target: str, client_id: str, percent: int) -> bool:
        bucket = int(
            hashlib.sha256(f"{name}:{environment}:{target}:{client_id}".encode("utf-8")).hexdigest()[:8],
            16,
        ) % 100
        return bucket < percent

    def _cache_version(self, version: ConfigVersion) -> None:
        payload = {
            "name": version.name,
            "environment": version.environment,
            "version": version.version,
            "value": version.value,
            "schema": version.schema,
            "labels": version.labels,
            "description": version.description,
            "created_at": version.created_at.isoformat(),
        }
        self.cache.set_json(self._latest_cache_key(version.name, version.environment), payload)
        self.cache.set_json(self._version_cache_key(version.name, version.environment, version.version), payload)

    async def _publish_event(
        self,
        *,
        event: str,
        config_name: str,
        environment: EnvironmentName,
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
            "environment": environment,
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
            assignment = self._get_or_create_assignment(session, rollout.config_name, rollout.target, rollout.environment)
            assignment.stable_version = rollout.to_version
            rollout.status = "promoted"
            rollout.rollback_reason = None
            self._create_audit(
                session,
                actor=actor,
                action=action,
                config_name=rollout.config_name,
                environment=rollout.environment,
                version=rollout.to_version,
                details={"target": rollout.target, "rollout_id": rollout_id, "reason": reason},
            )
            session.commit()
            ROLLOUT_EVENTS.labels("promoted", rollout.environment).inc()
            response = RolloutResponse.model_validate(rollout, from_attributes=True)
            event = {
                "config_name": rollout.config_name,
                "environment": rollout.environment,
                "target": rollout.target,
                "version": rollout.to_version,
                "stable_version": rollout.to_version,
                "rollout_percent": 100,
                "rollout_id": rollout.rollout_id,
            }
        logger.info(
            "rollout promoted",
            extra={
                "event": action,
                "context": {
                    "config_name": event["config_name"],
                    "environment": event["environment"],
                    "target": event["target"],
                    "rollout_id": event["rollout_id"],
                    "version": event["version"],
                    "reason": reason,
                },
            },
        )
        await self._publish_event(event="rollout_promoted", **event, reason=reason)
        return response

    def _create_audit(
        self,
        session,
        *,
        actor: Actor,
        action: str,
        config_name: str,
        environment: str,
        version: int | None,
        details: dict[str, Any],
    ) -> None:
        session.add(
            AuditLog(
                user_id=actor.user_id,
                action=action,
                config_name=config_name,
                environment=environment,
                version=version,
                details=details,
            )
        )

    def _compatibility_report(
        self,
        session,
        name: str,
        schema: dict[str, Any],
        environment: EnvironmentName,
    ) -> dict[str, Sequence[int]]:
        versions = session.execute(
            select(ConfigVersion).where(ConfigVersion.name == name, ConfigVersion.environment == environment)
        ).scalars().all()
        compatible: list[int] = []
        incompatible: list[int] = []
        for version in versions:
            if validate_payload(version.value, schema):
                incompatible.append(version.version)
            else:
                compatible.append(version.version)
        return {"compatible_versions": compatible, "incompatible_versions": incompatible}

    def _get_cached_version_payload(
        self,
        name: str,
        environment: EnvironmentName,
        version: int,
    ) -> dict[str, Any] | None:
        return self.cache.get_json(self._version_cache_key(name, environment, version))

    @staticmethod
    def _latest_cache_key(name: str, environment: EnvironmentName) -> str:
        return f"config:{environment}:{name}:latest"

    @staticmethod
    def _version_cache_key(name: str, environment: EnvironmentName, version: int) -> str:
        return f"config:{environment}:{name}:version:{version}"

    @staticmethod
    def _coerce_datetime(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _payload_to_read_response(
        self,
        payload: dict[str, Any],
        *,
        target: str,
        source: str,
    ) -> ConfigReadResponse:
        return ConfigReadResponse(
            name=payload["name"],
            environment=payload["environment"],
            version=payload["version"],
            target=target,
            source=source,
            description=payload.get("description"),
            labels=payload.get("labels", {}),
            value=payload["value"],
            schema_=payload["schema"],
            created_at=self._coerce_datetime(payload["created_at"]),
        )

    def _diff_values(self, before: Any, after: Any, path: str = "") -> list[ConfigDiffEntry]:
        if isinstance(before, dict) and isinstance(after, dict):
            changes: list[ConfigDiffEntry] = []
            all_keys = sorted(set(before) | set(after))
            for key in all_keys:
                child_path = f"{path}.{key}" if path else key
                if key not in before:
                    changes.append(ConfigDiffEntry(path=child_path, change_type="added", after=after[key]))
                elif key not in after:
                    changes.append(ConfigDiffEntry(path=child_path, change_type="removed", before=before[key]))
                else:
                    changes.extend(self._diff_values(before[key], after[key], child_path))
            return changes
        if isinstance(before, list) and isinstance(after, list):
            if before == after:
                return []
            return [ConfigDiffEntry(path=path or "$", change_type="changed", before=before, after=after)]
        if before != after:
            return [ConfigDiffEntry(path=path or "$", change_type="changed", before=before, after=after)]
        return []
