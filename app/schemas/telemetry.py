from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ConfigSource = Literal["explicit", "latest", "stable", "canary", "unknown"]


class FailureTelemetryRequest(BaseModel):
    config_name: str
    environment: str = "prod"
    target: str
    source: str = Field(min_length=1, max_length=80)
    error_type: str = Field(min_length=1, max_length=120)
    fingerprint: str = Field(min_length=16, max_length=64)
    anonymous_installation_id: str = Field(min_length=16, max_length=128)
    config_version: int | None = Field(default=None, ge=1)
    config_source: ConfigSource = "unknown"
    sdk_version: str | None = Field(default=None, max_length=40)
    app_version: str | None = Field(default=None, max_length=40)
    runtime: str | None = Field(default=None, max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureTelemetryResponse(BaseModel):
    event_id: str
    ingested_at: datetime
    fingerprint: str
    anonymous_installation_hash: str


class FailureTelemetryEventResponse(BaseModel):
    event_id: str
    config_name: str
    environment: str
    target: str
    source: str
    error_type: str
    fingerprint: str
    anonymous_installation_hash: str
    config_version: int | None
    config_source: str
    sdk_version: str | None
    app_version: str | None
    runtime: str | None
    metadata: dict
    occurred_at: datetime


class FailureTelemetrySummaryResponse(BaseModel):
    config_name: str
    environment: str
    target: str
    error_type: str
    fingerprint: str
    event_count: int
    distinct_installations: int
    last_seen: datetime
    latest_config_version: int | None
