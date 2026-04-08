from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router
from app.core.container import ServiceContainer
from app.core.logging import configure_logging
from app.core.metrics import metrics_middleware
from app.core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    container = ServiceContainer.build(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container
        await container.startup()
        yield
        await container.shutdown()

    app = FastAPI(title=resolved_settings.app_name, version="0.1.0", lifespan=lifespan)
    app.middleware("http")(metrics_middleware)
    app.include_router(router)
    return app


app = create_app()
