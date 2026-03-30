from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'config-service.db'}",
        use_redis=False,
        sdk_cache_dir=tmp_path / ".sdk-cache",
        canary_poll_interval_seconds=0.1,
        longpoll_timeout_seconds=1,
    )
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
