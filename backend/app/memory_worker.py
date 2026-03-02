from __future__ import annotations

import asyncio
import logging
import time

from app.core.config import get_settings
from app.db.init_db import init_db
from app.services.message_handler import scan_unprocessed_memory_messages


logger = logging.getLogger(__name__)


async def _run_once() -> None:
    settings = get_settings()
    await scan_unprocessed_memory_messages(
        max_conversations=max(1, int(settings.long_term_memory_scan_max_conversations or 80)),
        max_messages_per_conversation=max(
            1,
            int(settings.long_term_memory_scan_max_messages_per_conversation or 30),
        ),
    )


async def _run_loop() -> None:
    settings = get_settings()
    interval_sec = max(15, int(settings.long_term_memory_scan_interval_sec or 120))
    logger.info(
        "memory worker started: interval=%ss max_conversations=%s max_messages_per_conversation=%s",
        interval_sec,
        int(settings.long_term_memory_scan_max_conversations or 80),
        int(settings.long_term_memory_scan_max_messages_per_conversation or 30),
    )
    while True:
        started = time.perf_counter()
        try:
            if settings.long_term_memory_enabled and settings.long_term_memory_scan_enabled:
                await _run_once()
        except Exception:
            logger.exception("memory worker iteration failed")
        elapsed = time.perf_counter() - started
        sleep_sec = max(1, interval_sec - int(elapsed))
        await asyncio.sleep(sleep_sec)


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
