"""SSE endpoint for real-time events to the portal."""

import asyncio
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from sqlalchemy import select

from app.database import async_session_factory
from app.models.user import User
from app.services.auth_service import decode_token
from app.services.event_service import event_service

router = APIRouter(prefix="/events", tags=["events"])

KEEPALIVE_INTERVAL = 25  # seconds


async def _authenticate_token(token: str) -> User:
    """Validate JWT from query param and return the user.

    SSE (EventSource) doesn't support custom headers, so the token
    is passed as a query parameter instead of Authorization header.
    """
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    async with async_session_factory() as db:
        result = await db.execute(
            select(User).where(User.id == uuid.UUID(user_id_str))
        )
        user = result.scalars().first()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


async def _event_generator(client_id: str) -> AsyncGenerator[str, None]:
    """Yields SSE-formatted events from the client's queue."""
    queue = event_service.subscribe(client_id)
    try:
        # Send initial retry directive (5s reconnect) and a connected event
        yield "retry: 5000\n\n"
        yield f"event: connected\ndata: {{\"client_id\": \"{client_id}\"}}\n\n"

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
                yield f"data: {payload}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment to prevent proxy/browser timeouts
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        event_service.unsubscribe(client_id)


@router.get("/stream")
async def event_stream(token: str = Query(..., description="JWT token")) -> StreamingResponse:
    """SSE endpoint. Connect with EventSource('...?token=<jwt>').

    Events are JSON with an 'event_type' field:
    - sync_push_completed: inspector pushed data (portal should refresh stats/entries)
    - assignment_changed: manager changed inspector access (portal should refresh users)
    """
    await _authenticate_token(token)
    client_id = str(uuid.uuid4())
    return StreamingResponse(
        _event_generator(client_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
        },
    )
