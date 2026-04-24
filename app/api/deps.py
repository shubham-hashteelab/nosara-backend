import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.user import User
from app.services.auth_service import decode_token

security = HTTPBearer()


async def get_db() -> AsyncSession:  # type: ignore[misc]
    async with async_session_factory() as session:
        yield session


async def _authenticate(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> User:
    """Decode the bearer token and return the active User row. Does not apply
    any role-based gating — callers layer that on top."""
    token = credentials.credentials
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


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Default auth dep. Rejects CONTRACTOR so existing inspector/manager
    routes can adopt this without per-route role checks and stay safe."""
    user = await _authenticate(credentials, db)
    if user.role == "CONTRACTOR":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contractor role cannot access this endpoint",
        )
    return user


async def get_current_user_allow_all(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Auth dep that accepts every active role including CONTRACTOR. Use only
    on routes that branch on role internally (e.g. sync push/pull, file
    upload) or that specifically serve contractors via require_contractor."""
    return await _authenticate(credentials, db)


async def require_manager(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != "MANAGER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role required",
        )
    return current_user


async def require_inspector(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != "INSPECTOR":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inspector role required",
        )
    return current_user


async def require_contractor(
    current_user: Annotated[User, Depends(get_current_user_allow_all)],
) -> User:
    if current_user.role != "CONTRACTOR":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contractor role required",
        )
    return current_user
