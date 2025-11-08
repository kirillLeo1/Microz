# app/db.py
import os
import logging
import random
import asyncio
from typing import Optional

import asyncpg

log = logging.getLogger("db")

_pool: Optional[asyncpg.pool.Pool] = None


def _pool_sizes():
    # Мелкий пул, чтобы не упираться в лимиты Railway
    min_size = int(os.getenv("DB_POOL_MIN", "1"))
    max_size = int(os.getenv("DB_POOL_MAX", "3"))
    if max_size < min_size:
        max_size = min_size
    return min_size, max_size


async def _init_connection(con: asyncpg.Connection):
    # лёгкий "ping": убедимся, что канал живой и таймзона норм
    try:
        await con.execute("SELECT 1;")
    except Exception:
        pass
    # если хочешь — выставь таймзону
    # await con.execute("SET TIME ZONE 'UTC';")


async def connect() -> asyncpg.pool.Pool:
    """
    Подключаемся к БД строго по DSN из ENV, без модификаций.
    Делаем до 10 попыток с экспоненциальной задержкой и логируем ПРИЧИНУ ошибки.
    """
    global _pool
    if _pool is not None:
        return _pool

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")

    min_size, max_size = _pool_sizes()
    command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", "60"))

    last_err = None
    delay = 0.5
    for attempt in range(1, 11):
        try:
            _pool = await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                command_timeout=command_timeout,
                init=_init_connection,  # пингуем соединения при выдаче
                max_inactive_connection_lifetime=60,  # убираем подвисшие
            )
            log.info("DB pool created (min=%s, max=%s)", min_size, max_size)
            return _pool
        except Exception as e:
            last_err = e
            # Пишем тип и текст, чтобы понимать что именно
            log.warning("DB connect failed (try %d/10): %s: %s", attempt, type(e).__name__, e)
            # Railway часто ругается TooManyConnections/CannotConnectNow сразу после перезапуска —
            # подождём и попробуем снова
            await asyncio.sleep(delay + random.uniform(0, 0.4))
            delay = min(delay * 2, 8.0)

    # 10 попыток не помогли — отдаём исходную ошибку
    raise last_err


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _acquire():
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    return await _pool.acquire()


async def execute(query: str, *args):
    async with _pool.acquire() as con:
        return await con.execute(query, *args)


async def fetch(query: str, *args):
    async with _pool.acquire() as con:
        return await con.fetch(query, *args)


async def fetchrow(query: str, *args):
    async with _pool.acquire() as con:
        return await con.fetchrow(query, *args)


async def fetchval(query: str, *args):
    async with _pool.acquire() as con:
        return await con.fetchval(query, *args)

