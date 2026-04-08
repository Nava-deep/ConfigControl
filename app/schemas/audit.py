from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditEntryResponse(BaseModel):
    id: str
    user_id: str
    action: str
    config_name: str
    environment: str
    version: int | None
    timestamp: datetime
    details: dict
