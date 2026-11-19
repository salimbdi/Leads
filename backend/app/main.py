from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import platform

import fastapi
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sqlalchemy

from backend.app.config import get_settings
from backend.app.db import get_schema_metadata, init_db, verify_database_connection
from backend.app.routers import integrations_router, leads_router, search_runs_router


from backend.app.services.worker_service import (
    start_background_worker,
    stop_background_worker,
)

logger = logging.getLogger("backend.app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Initialize SQLite tables and optionally start embedded Python background worker on FastAPI startup.
    Embedded worker starts ONLY when WORKER_ENABLED=true is explicitly configured.
    DISCOVERY_EXECUTION_MODE=python alone enqueues tasks without starting an in-process worker thread.
    """
    init_db()
    current_settings = get_settings()
    diag_msg = current_settings.format_runtime_diagnostic_log(component="FastAPI")
    logger.info(diag_msg)
    print(diag_msg, flush=True)
    should_run_worker = bool(current_settings.worker_enabled)
    if should_run_worker:
        start_background_worker(
            mock_mode=(current_settings.ai_provider == "mock")
        )
    try:
        yield
    finally:
        if should_run_worker:
            stop_background_worker(
                timeout=current_settings.worker_shutdown_timeout_seconds
            )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.app_name} API",
        description=(
            "LeadForge business discovery and deterministic lead qualification backend. "
            "Phase 4: n8n Integration."
        ),
        version=settings.app_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register REST API and n8n integration routers
    app.include_router(search_runs_router)
    app.include_router(leads_router)
    app.include_router(integrations_router)

    def build_health_payload() -> dict[str, object]:
        current_settings = get_settings()
        db_status = verify_database_connection()
        return {
            "status": "ok",
            "service": "leadforge-backend",
            "app_name": current_settings.app_name,
            "version": current_settings.app_version,
            "environment": current_settings.app_env,
            "phase": "Phase 4 — n8n Integration",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runtime": {
                "python_version": platform.python_version(),
                "fastapi_version": fastapi.__version__,
                "sqlalchemy_version": sqlalchemy.__version__,
            },
            "database": db_status,
            "config": current_settings.get_public_summary(),
            "diagnostic": current_settings.get_runtime_diagnostic(),
        }

    @app.get("/health", tags=["system"])
    async def root_health_check() -> dict[str, object]:
        """Primary backend health check endpoint."""
        return build_health_payload()

    @app.get("/api/health", tags=["system"])
    async def api_health_check() -> dict[str, object]:
        """API-namespaced health check endpoint for frontend proxy consumption."""
        return build_health_payload()

    @app.get("/api/v1/config/diagnostic", tags=["system"])
    async def runtime_config_diagnostic() -> dict[str, object]:
        """Return safe non-secret runtime configuration diagnostic metadata."""
        return get_settings().get_runtime_diagnostic()

    @app.get("/api/v1/foundation", tags=["system"])
    async def foundation_status() -> dict[str, object]:
        """Return project foundation, database, API, and Phase 4 n8n integration status."""
        current_settings = get_settings()
        return {
            "status": "ready",
            "current_phase": 4,
            "phase_title": "Phase 4 — n8n Integration",
            "architecture_flow": [
                {"step": 1, "component": "React Dashboard", "role": "Search & lead qualification UI", "status": "active"},
                {"step": 2, "component": "FastAPI Backend", "role": "REST API, validation & deterministic scoring", "status": "active"},
                {"step": 3, "component": "n8n Webhook", "role": "Workflow orchestration trigger", "status": "active"},
                {"step": 4, "component": "Apify + AI Analysis", "role": "Business discovery & structured evidence extraction", "status": "active"},
                {"step": 5, "component": "FastAPI Callback", "role": "Idempotent ingestion & deterministic lead scoring", "status": "active"},
                {"step": 6, "component": "SQLite + SQLAlchemy", "role": "Single source of truth persistence", "status": "active"},
            ],
            "config_summary": current_settings.get_public_summary(),
        }

    @app.get("/api/v1/database/schema", tags=["database"])
    async def database_schema_inspection() -> dict[str, object]:
        """Return verified SQLite table, column, foreign key, and constraint metadata."""
        return get_schema_metadata()

    return app


app = create_app()
