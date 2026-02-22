from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.webhooks import router as webhook_router
from app.api.endpoints.client import router as client_router
from app.api.admin import router as admin_router
from app.core.config import get_settings
from app.db.init_db import init_db
import logging
import asyncio

from app.services.scheduler import get_scheduler
from app.services.telegram_polling import telegram_polling_loop
from app.services.scheduler_tasks import restore_pending_reminder_jobs
from app.graph.workflow import close_graph
from app.db.session import AsyncSessionLocal
from app.services.memory import deactivate_all_identity_memories


settings = get_settings()
app = FastAPI(title=settings.app_name)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(client_router)
app.include_router(admin_router)

_background_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    try:
        async with AsyncSessionLocal() as session:
            cleared = await deactivate_all_identity_memories(session)
            if cleared > 0:
                logger.info("startup memory cleanup: deleted %s identity memories", cleared)
    except Exception:
        logger.exception("startup memory cleanup failed")
    get_scheduler().start()
    await restore_pending_reminder_jobs()
    if settings.telegram_polling_enabled:
        _background_tasks.append(asyncio.create_task(telegram_polling_loop()))


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in _background_tasks:
        task.cancel()
    await close_graph()
