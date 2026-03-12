from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import assets as assets_router
from .routers import auth as auth_router
from .routers import config as config_router
from .routers import documents as documents_router
from .routers import entities as entities_router
from .routers import ingest as ingest_router
from .routers import notifications as notifications_router
from .routers import obligations as obligations_router
from .routers import risks as risks_router
from .routers import summaries as summaries_router
from .routers import users as users_router


def create_app() -> FastAPI:
    app = FastAPI(title="VeritasLayer API")

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    app.include_router(ingest_router)
    app.include_router(documents_router)
    app.include_router(obligations_router)
    app.include_router(risks_router)
    app.include_router(entities_router)
    app.include_router(summaries_router)
    app.include_router(assets_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(notifications_router)
    app.include_router(config_router)

    return app


app = create_app()
