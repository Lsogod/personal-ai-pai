from __future__ import annotations

import asyncio
import logging
import time

from app.core.config import get_settings
from app.db.init_db import init_db
from app.db.session import AsyncSessionLocal
from app.services.memory import sync_long_term_memory_vectors
from app.services.memory_vector_store import ensure_memory_vector_collection, milvus_enabled

logger = logging.getLogger(__name__)


async def _run_once() -> None:
    settings = get_settings()
    if not settings.memory_index_worker_enabled or not milvus_enabled():
        return
    await ensure_memory_vector_collection()
    async with AsyncSessionLocal() as session:
        result = await sync_long_term_memory_vectors(
            session,
            batch_size=max(1, int(settings.memory_index_worker_batch_size or 32)),
        )
    if result["scanned"] > 0:
        logger.info("memory index sync: scanned=%s synced=%s failed=%s", result["scanned"], result["synced"], result["failed"])


async def _run_loop() -> None:
    settings = get_settings()
    interval_sec = max(10, int(settings.memory_index_worker_interval_sec or 30))
    logger.info(
        "memory index worker started: enabled=%s milvus_enabled=%s interval=%ss batch_size=%s",
        bool(settings.memory_index_worker_enabled),
        bool(milvus_enabled()),
        interval_sec,
        int(settings.memory_index_worker_batch_size or 32),
    )
    while True:
        started = time.perf_counter()
        try:
            await _run_once()
        except Exception:
            logger.exception("memory index worker iteration failed")
        elapsed = time.perf_counter() - started
        await asyncio.sleep(max(1, interval_sec - int(elapsed)))


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=str(settings.log_level or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    await init_db()
    await _run_loop()


if __name__ == "__main__":
    asyncio.run(main())
