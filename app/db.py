# app/db.py
from __future__ import annotations

import ssl
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from .config import settings

# --- SSL контекст "як sslmode=require" (без перевірки ланцюга) ---
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE  # рівно як у ?sslmode=require

# УВАГА: для asyncpg параметр SSL передається через connect_args={"ssl": ssl_ctx}
engine = create_async_engine(
    settings.DATABASE_URL,        # формат: postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DB
    pool_pre_ping=True,
    connect_args={"ssl": ssl_ctx},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

