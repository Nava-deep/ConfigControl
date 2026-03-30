from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CanaryCheck(BaseModel):
    metric: str
    threshold: float = Field(gt=0)
    window: int = Field(default=5, ge=1, description="Window in minutes before automatic promotion.")


class ConfigCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    value: dict[str, Any]
    description: str | None = None


class ConfigVersionResponse(BaseModel):
    config_id: str
    name: str
    version: int
    description: str | None
    created_by: str
    created_at: datetime
    active_target: str | None = None
    activated: bool
    warnings: list[str] = Field(default_factory=list)


class ConfigSummary(BaseModel):
    name: str
    latest_version: int
    stable_target: str
    stable_version: int
    updated_at: datetime


class ConfigReadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    version: int
    target: str
    source: Literal["explicit", "latest", "stable", "canary"]
    description: str | None
    value: dict[str, Any]
    schema_: dict[str, Any] = Field(alias="schema")
    created_at: datetime


class RolloutRequest(BaseModel):
    target: str
    percent: int = Field(ge=1, le=100)
    canary_check: CanaryCheck | None = None


class RolloutResponse(BaseModel):
    rollout_id: str
    config_name: str
    target: str
    from_version: int
    to_version: int
    percent: int
    status: str
    created_at: datetime
    rollback_reason: str | None = None


class RollbackRequest(BaseModel):
    target_version: int = Field(ge=1)
    target: str | None = None


class VersionHistoryEntry(BaseModel):
    version: int
    created_at: datetime
    created_by: str
    description: str | None
    is_latest: bool


class DryRunMigrationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: dict[str, Any] = Field(alias="schema")
    value: dict[str, Any] | None = None


class DryRunMigrationResponse(BaseModel):
    config_name: str
    candidate_value_valid: bool
    current_versions_checked: int
    compatible_versions: list[int]
    incompatible_versions: list[int]
    issues: list[str] = Field(default_factory=list)


class NotificationEvent(BaseModel):
    sequence: int
    event: str
    config_name: str
    target: str
    version: int
    stable_version: int
    rollout_percent: int | None = None
    rollout_id: str | None = None
    reason: str | None = None
    timestamp: datetime


class SimulationMetricUpdate(BaseModel):
    target: str
    metric: str
    value: float = Field(ge=0)


class SimulationMetricResponse(BaseModel):
    target: str
    metric: str
    value: float
    timestamp: datetime
