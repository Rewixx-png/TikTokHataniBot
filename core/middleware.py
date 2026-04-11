from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session


class DatabaseSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async for session in get_session():
            data['session'] = session
            try:
                return await handler(event, data)
            except Exception:
                await session.rollback()
                raise
