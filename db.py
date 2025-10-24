import asyncpg
from .config import settings

pool: asyncpg.Pool | None = None

async def connect():
    global pool
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=10)

async def close():
    global pool
    if pool:
        await pool.close()

async def execute(sql: str, *args):
    assert pool
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)

async def fetch(sql: str, *args):
    assert pool
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)

async def fetchrow(sql: str, *args):
    assert pool
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)

async def fetchval(sql: str, *args):
    assert pool
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)
