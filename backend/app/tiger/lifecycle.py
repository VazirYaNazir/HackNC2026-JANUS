"""Starts and stops the optional Tiger Data integration alongside the app. Does nothing without TIGER_DATABASE_URL."""
import logging
from typing import Any

from .connection import tiger_url
from .sink import TigerSink
from .store import TigerStore
from .writer import get_writer

logger = logging.getLogger("uvicorn.error")


async def start_tiger(app: Any, runtime: Any) -> None:
    writer = get_writer()
    url = tiger_url()
    app.state.tiger_writer = writer
    app.state.tiger_store = TigerStore(url) if url else None
    await writer.start()
    native = getattr(runtime, "native", None)
    if native is not None and writer.enabled:
        native.sink = TigerSink(writer)
        logger.info("Tiger Data ingestion enabled (metrics, process samples, action events)")


async def stop_tiger(app: Any) -> None:
    writer = getattr(app.state, "tiger_writer", None)
    if writer is not None:
        await writer.stop()
