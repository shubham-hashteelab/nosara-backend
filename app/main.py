import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import async_session_factory
from app.services.auth_service import create_default_manager
from app.services.event_service import event_service
from app.services.inspection_service import backfill_uninitialized_flats
from app.services.minio_service import minio_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    # Startup
    logger.info("Starting up Nosara backend...")

    # Create default manager if no users exist
    try:
        async with async_session_factory() as db:
            await create_default_manager(db)
            logger.info("Default manager check complete.")
    except Exception as exc:
        logger.warning("Could not check/create default manager (tables may not exist yet): %s", exc)

    # Ensure MinIO bucket exists
    try:
        minio_service.ensure_bucket()
        logger.info("MinIO bucket ready.")
    except Exception as exc:
        logger.warning("Could not ensure MinIO bucket (service may be unavailable): %s", exc)

    # Start PG LISTEN for SSE broadcasting
    try:
        await event_service.start_listener()
        logger.info("SSE event listener started.")
    except Exception as exc:
        logger.warning("Could not start event listener: %s", exc)

    # Backfill inspection entries for any flat that has none. Covers flats
    # created before auto-init-on-create shipped.
    try:
        async with async_session_factory() as db:
            await backfill_uninitialized_flats(db)
    except Exception as exc:
        logger.warning("Checklist backfill skipped: %s", exc)

    yield

    # Shutdown
    await event_service.stop_listener()
    logger.info("Shutting down Nosara backend...")


app = FastAPI(
    title="Nosara Snagging Inspection API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
from app.api.auth import router as auth_router  # noqa: E402
from app.api.users import router as users_router  # noqa: E402
from app.api.projects import router as projects_router  # noqa: E402
from app.api.buildings import router as buildings_router  # noqa: E402
from app.api.floors import router as floors_router  # noqa: E402
from app.api.flats import router as flats_router  # noqa: E402
from app.api.inspections import router as inspections_router  # noqa: E402
from app.api.media import router as media_router  # noqa: E402
from app.api.ai import router as ai_router  # noqa: E402
from app.api.contractors import router as contractors_router  # noqa: E402
from app.api.checklists import router as checklists_router  # noqa: E402
from app.api.dashboard import router as dashboard_router  # noqa: E402
from app.api.sync import router as sync_router  # noqa: E402
from app.api.events import router as events_router  # noqa: E402

API_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(projects_router, prefix=API_PREFIX)
app.include_router(buildings_router, prefix=API_PREFIX)
app.include_router(floors_router, prefix=API_PREFIX)
app.include_router(flats_router, prefix=API_PREFIX)
app.include_router(inspections_router, prefix=API_PREFIX)
app.include_router(media_router, prefix=API_PREFIX)
app.include_router(ai_router, prefix=API_PREFIX)
app.include_router(contractors_router, prefix=API_PREFIX)
app.include_router(checklists_router, prefix=API_PREFIX)
app.include_router(dashboard_router, prefix=API_PREFIX)
app.include_router(sync_router, prefix=API_PREFIX)
app.include_router(events_router, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health")
async def health_check() -> dict:
    return {"status": "ok", "version": "1.0.0"}
