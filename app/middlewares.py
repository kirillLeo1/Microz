from aiogram import BaseMiddleware
from typing import Callable, Dict, Any, Awaitable
from .db import AsyncSessionLocal

class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Dict[str, Any], Any], Awaitable[Any]], event: Any, data: Dict[str, Any]) -> Any:
        async with AsyncSessionLocal() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result