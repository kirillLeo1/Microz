# app/utils/db.py
import os
import logging
import asyncpg

log = logging.getLogger("db")

_pool: asyncpg.pool.Pool | None = None


async def connect():
    """
    Подключаемся к БД строго по тому DSN, что задан в окружении.
    НИКАКИХ правок строки: без sslmode=require, без SSL-контекстов и т.п.
    Оставляем поведение как было у тебя раньше.
    """
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")

    # Пул делаем компактным, чтобы не душить Railway.
    min_size = int(os.getenv("DB_POOL_MIN", "1"))
    max_size = int(os.getenv("DB_POOL_MAX", "5"))
    command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", "60"))

    # ВАЖНО: передаём DSN «как есть», НЕ указываем ssl=..., НЕ модифицируем строку.
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )
    log.info("DB pool created")


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


async def fetchrow(query: str, *args):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.fetchrow(query, *args)


async def fetch(query: str, *args):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as con:
        return await con.fetch(query, *args)

