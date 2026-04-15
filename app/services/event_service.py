"""
Server-Sent Events broadcaster using PostgreSQL LISTEN/NOTIFY.

Each uvicorn worker runs its own listener on the 'nosara_events' PG channel.
When any worker fires NOTIFY, PostgreSQL delivers it to all listeners across
all workers. Each listener then fans out to its in-process SSE clients.
"""

import asyncio
import json
import logging
from typing import Any

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

# Channel name used for PG LISTEN/NOTIFY
PG_CHANNEL = "nosara_events"


def _get_raw_dsn() -> str:
    """Convert SQLAlchemy async DSN to raw asyncpg DSN."""
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


class EventService:
    def __init__(self) -> None:
        # client_id -> asyncio.Queue — one queue per connected SSE client
        self._clients: dict[str, asyncio.Queue[str]] = {}
        self._listener_conn: asyncpg.Connection | None = None
        self._listener_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # SSE client management
    # ------------------------------------------------------------------

    def subscribe(self, client_id: str) -> asyncio.Queue[str]:
        """Register a new SSE client. Returns its event queue."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
        self._clients[client_id] = queue
        logger.info("SSE client connected: %s (total: %d)", client_id, len(self._clients))
        return queue

    def unsubscribe(self, client_id: str) -> None:
        """Remove an SSE client."""
        self._clients.pop(client_id, None)
        logger.info("SSE client disconnected: %s (total: %d)", client_id, len(self._clients))

    # ------------------------------------------------------------------
    # PG NOTIFY — called by API endpoints after commits
    # ------------------------------------------------------------------

    async def notify(self, payload: dict[str, Any]) -> None:
        """Send a NOTIFY on the PG channel. Best-effort — failures are logged, not raised."""
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(_get_raw_dsn())
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                PG_CHANNEL,
                json.dumps(payload),
            )
        except Exception:
            logger.exception("Failed to send PG NOTIFY")
        finally:
            if conn is not None:
                await conn.close()

    # ------------------------------------------------------------------
    # PG LISTEN — background task per worker
    # ------------------------------------------------------------------

    async def start_listener(self) -> None:
        """Start the background PG LISTEN loop."""
        self._listener_task = asyncio.create_task(self._listen_loop())
        logger.info("PG LISTEN started on channel '%s'", PG_CHANNEL)

    async def stop_listener(self) -> None:
        """Stop the background listener and close the PG connection."""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._listener_conn is not None:
            await self._listener_conn.close()
            self._listener_conn = None
        logger.info("PG LISTEN stopped")

    async def _listen_loop(self) -> None:
        """Long-running loop: connect to PG, LISTEN, and fan out notifications."""
        while True:
            try:
                self._listener_conn = await asyncpg.connect(_get_raw_dsn())
                await self._listener_conn.add_listener(PG_CHANNEL, self._on_notification)
                logger.info("PG listener connected and subscribed")

                # Keep alive until cancelled or connection drops
                while True:
                    await asyncio.sleep(60)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PG listener error, reconnecting in 2s")
                if self._listener_conn is not None:
                    try:
                        await self._listener_conn.close()
                    except Exception:
                        pass
                    self._listener_conn = None
                await asyncio.sleep(2)

    def _on_notification(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        """Callback fired by asyncpg when a NOTIFY arrives. Fans out to all SSE clients."""
        stale_clients: list[str] = []

        for client_id, queue in self._clients.items():
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale_clients.append(client_id)
                logger.warning("SSE queue full for client %s, dropping", client_id)

        for client_id in stale_clients:
            self._clients.pop(client_id, None)


event_service = EventService()
