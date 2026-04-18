from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EnvironmentName = Literal["dev", "staging", "prod"]


class CanaryCheck(BaseModel):
    metric: str
    threshold: float = Field(gt=0)
    window: int = Field(default=5, ge=1, description="Window in minutes before automatic promotion.")


class ConfigCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    environment: EnvironmentName = "prod"
    labels: dict[str, str] = Field(default_factory=dict)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    value: dict[str, Any]
    description: str | None = None


class ConfigVersionResponse(BaseModel):
    config_id: str
    name: str
    environment: EnvironmentName
    version: int
    labels: dict[str, str]
    description: str | None
    created_by: str
    created_at: datetime
    active_target: str | None = None
    activated: bool
    warnings: list[str] = Field(default_factory=list)


class ConfigSummary(BaseModel):
    name: str
    environment: EnvironmentName
    latest_version: int
    stable_target: str
    stable_version: int
    updated_at: datetime


class ConfigReadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    environment: EnvironmentName
    version: int
    target: str
    source: Literal["explicit", "latest", "stable", "canary"]
    description: str | None
    labels: dict[str, str]
    value: dict[str, Any]
    schema_: dict[str, Any] = Field(alias="schema")
    created_at: datetime


class RolloutRequest(BaseModel):
    target: str
    environment: EnvironmentName = "prod"
    percent: int = Field(ge=1, le=100)
    canary_check: CanaryCheck | None = None


class RolloutAdvanceRequest(BaseModel):
    percent: int = Field(ge=1, le=100)


class RolloutResponse(BaseModel):
    rollout_id: str
    config_name: str
    environment: EnvironmentName
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
    environment: EnvironmentName = "prod"


class VersionHistoryEntry(BaseModel):
    environment: EnvironmentName
    version: int
    created_at: datetime
    created_by: str
    labels: dict[str, str]
    description: str | None
    is_latest: bool


class DryRunMigrationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    environment: EnvironmentName = "prod"
    schema_: dict[str, Any] = Field(alias="schema")
    value: dict[str, Any] | None = None


class DryRunMigrationResponse(BaseModel):
    config_name: str
    environment: EnvironmentName
    candidate_value_valid: bool
    current_versions_checked: int
    compatible_versions: list[int]
    incompatible_versions: list[int]
    issues: list[str] = Field(default_factory=list)


class NotificationEvent(BaseModel):
    sequence: int
    event: str
    config_name: str
    environment: EnvironmentName
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


class ConfigDiffEntry(BaseModel):
    path: str
    change_type: Literal["added", "removed", "changed"]
    before: Any | None = None
    after: Any | None = None


class ConfigDiffResponse(BaseModel):
    config_name: str
    environment: EnvironmentName
    from_version: int
    to_version: int
    changes: list[ConfigDiffEntry]
