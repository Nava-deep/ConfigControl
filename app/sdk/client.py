from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import platform
import time
import traceback
from uuid import uuid4
from pathlib import Path
from typing import Any, Generic, TypeVar
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from pydantic import BaseModel
from websockets.asyncio.client import connect

from app.schemas.config import EnvironmentName


ModelT = TypeVar("ModelT", bound=BaseModel)
SDK_VERSION = "0.1.0"


class ConfigClient(Generic[ModelT]):
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        target: str,
        environment: EnvironmentName = "prod",
        ttl_seconds: int = 30,
        cache_dir: Path | None = None,
        user_id: str = "sdk-client",
        role: str = "reader",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.target = target
        self.environment = environment
        self.ttl_seconds = ttl_seconds
        self.cache_dir = cache_dir or Path(".cache/config-sdk")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._anonymous_installation_id = self._load_or_create_installation_id()
        self._last_seen_payloads: dict[str, dict[str, Any]] = {}
        self._auth_headers = {"X-User-Id": user_id, "X-Role": role}
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers=self._auth_headers,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, name: str, *, version: str = "resolved", force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh:
            cached = self._load_cache(name, version)
            if cached and self._is_fresh(cached):
                self._last_seen_payloads[name] = cached["payload"]
                return cached["payload"]
        try:
            response = self._client.get(
                f"/configs/{name}",
                params={
                    "version": version,
                    "target": self.target,
                    "client_id": self.client_id,
                    "environment": self.environment,
                },
            )
            response.raise_for_status()
            payload = response.json()
            self._save_cache(name, version, payload)
            self._last_seen_payloads[name] = payload
            return payload
        except Exception:
            cached = self._load_cache(name, version)
            if cached:
                self._last_seen_payloads[name] = cached["payload"]
                return cached["payload"]
            raise

    def get_typed(self, name: str, model: type[ModelT], *, version: str = "resolved", force_refresh: bool = False) -> ModelT:
        payload = self.get(name, version=version, force_refresh=force_refresh)
        return model.model_validate(payload["value"])

    async def watch(self, name: str, model: type[ModelT], callback) -> None:
        ws_url = self._build_ws_url(name)
        while True:
            try:
                async with connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    additional_headers=self._auth_headers,
                ) as websocket:
                    async for message in websocket:
                        event = json.loads(message)
                        if event.get("event") == "connected":
                            continue
                        config = await asyncio.to_thread(self.get_typed, name, model, version="resolved", force_refresh=True)
                        outcome = callback(config, event)
                        if inspect.isawaitable(outcome):
                            await outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    def report_failure(
        self,
        name: str,
        error: BaseException,
        *,
        source: str,
        app_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        context = self._last_seen_payloads.get(name, {})
        payload = {
            "config_name": name,
            "environment": self.environment,
            "target": self.target,
            "source": source,
            "error_type": type(error).__name__,
            "fingerprint": self._build_fingerprint(error),
            "anonymous_installation_id": self._anonymous_installation_id,
            "config_version": context.get("version"),
            "config_source": context.get("source", "unknown"),
            "sdk_version": SDK_VERSION,
            "app_version": app_version,
            "runtime": f"python-{platform.python_version()}",
            "metadata": self._sanitize_failure_metadata(metadata or {}),
        }
        try:
            response = self._client.post("/telemetry/failures", json=payload, timeout=2.0)
            response.raise_for_status()
        except Exception:
            # Failure telemetry is best-effort and should never take the app down.
            return

    def _cache_path(self, name: str, version: str) -> Path:
        safe_name = name.replace("/", "_").replace(".", "_")
        safe_version = str(version).replace("/", "_").replace(".", "_")
        safe_environment = self.environment.replace("/", "_").replace(".", "_")
        return self.cache_dir / f"{safe_name}_{safe_environment}_{safe_version}_{self.target}_{self.client_id}.json"

    def _save_cache(self, name: str, version: str, payload: dict[str, Any]) -> None:
        record = {"saved_at": time.time(), "payload": payload}
        self._cache_path(name, version).write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _load_cache(self, name: str, version: str) -> dict[str, Any] | None:
        path = self._cache_path(name, version)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _is_fresh(self, cached: dict[str, Any]) -> bool:
        saved_at = float(cached.get("saved_at", 0.0))
        if saved_at == 0.0:
            return False
        return (time.time() - saved_at) < self.ttl_seconds

    def _load_or_create_installation_id(self) -> str:
        path = self.cache_dir / "anonymous_installation_id"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        installation_id = uuid4().hex
        path.write_text(installation_id, encoding="utf-8")
        return installation_id

    @staticmethod
    def _build_fingerprint(error: BaseException) -> str:
        frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
        signature = [type(error).__name__]
        signature.extend(f"{Path(frame.filename).name}:{frame.name}" for frame in frames[-5:])
        return hashlib.sha256("|".join(signature).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _sanitize_failure_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in list(metadata.items())[:8]:
            if isinstance(value, bool | int | float) or value is None:
                sanitized[key] = value
            elif isinstance(value, str):
                sanitized[key] = value[:120]
        return sanitized

    def _build_ws_url(self, name: str) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode({"config_name": name, "environment": self.environment, "target": self.target})
        return urlunparse((scheme, parsed.netloc, "/watch/ws", "", query, ""))
