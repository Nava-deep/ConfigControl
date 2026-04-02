from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CONFIG_SERVICE_", extra="ignore")

    app_name: str = "Config Control Plane"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8080
    database_url: str = "sqlite:///./config_service.db"
    redis_url: str = "redis://localhost:6379/0"
    use_redis: bool = True
    canary_poll_interval_seconds: float = 1.0
    longpoll_timeout_seconds: int = 25
    request_timeout_seconds: float = 5.0
    sdk_cache_dir: Path = Field(default_factory=lambda: Path(".cache/config-sdk"))
    default_target: str = "default"
    websocket_reconnect_seconds: float = 1.0
    notification_channel: str = "config-events"
    instance_id: str = Field(default_factory=lambda: uuid4().hex)
    telemetry_hash_salt: str = "config-control-plane-demo-salt"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
