from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.webhooks import router as webhook_router
from app.api.endpoints.client import router as client_router
from app.api.admin import router as admin_router
from app.core.config import get_settings
from app.db.init_db import init_db
import asyncio

from app.services.scheduler import get_scheduler
from app.services.telegram_polling import telegram_polling_loop
from app.graph.workflow import close_graph


settings = get_settings()
app = FastAPI(title=settings.app_name)

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
    get_scheduler().start()
    if settings.telegram_polling_enabled:
        _background_tasks.append(asyncio.create_task(telegram_polling_loop()))


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in _background_tasks:
        task.cancel()
    await close_graph()
