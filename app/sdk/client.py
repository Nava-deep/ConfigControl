from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any, Generic, TypeVar
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from pydantic import BaseModel
from websockets.asyncio.client import connect


ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigClient(Generic[ModelT]):
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        target: str,
        ttl_seconds: int = 30,
        cache_dir: Path | None = None,
        user_id: str = "sdk-client",
        role: str = "reader",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.target = target
        self.ttl_seconds = ttl_seconds
        self.cache_dir = cache_dir or Path(".cache/config-sdk")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
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
                return cached["payload"]
        try:
            response = self._client.get(
                f"/configs/{name}",
                params={"version": version, "target": self.target, "client_id": self.client_id},
            )
            response.raise_for_status()
            payload = response.json()
            self._save_cache(name, version, payload)
            return payload
        except Exception:
            cached = self._load_cache(name, version)
            if cached:
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
                        config = self.get_typed(name, model, force_refresh=True)
                        outcome = callback(config, event)
                        if inspect.isawaitable(outcome):
                            await outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    def _cache_path(self, name: str, version: str) -> Path:
        safe_name = name.replace("/", "_").replace(".", "_")
        safe_version = str(version).replace("/", "_").replace(".", "_")
        return self.cache_dir / f"{safe_name}_{safe_version}_{self.target}_{self.client_id}.json"

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

    def _build_ws_url(self, name: str) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode({"config_name": name, "target": self.target})
        return urlunparse((scheme, parsed.netloc, "/watch/ws", "", query, ""))
