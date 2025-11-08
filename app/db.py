# app/db.py
import os
import logging
from typing import Optional

import asyncpg

log = logging.getLogger("db")

_pool: Optional[asyncpg.pool.Pool] = None


async def connect() -> asyncpg.pool.Pool:
    """
    Подключаемся к БД строго по тому DSN, что задан в окружении.
    Ничего в строке не дописываем и не меняем.
    """
    global _pool
    if _pool is not None:
        return _pool

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")

    min_size = int(os.getenv("DB_POOL_MIN", "1"))
    max_size = int(os.getenv("DB_POOL_MAX", "5"))
    command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", "60"))

    _pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )
    log.info("DB pool created")
    return _pool


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def execute(query: str, *args):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.execute(query, *args)


async def fetch(query: str, *args):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.fetch(query, *args)


async def fetchrow(query: str, *args):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.fetchrow(query, *args)


async def fetchval(query: str, *args):
    """
    Возвращает одно скалярное значение (SELECT count(*), SELECT id FROM ... LIMIT 1, и т.п.)
    """
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.fetchval(query, *args)
