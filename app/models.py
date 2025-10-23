# app/models.py
from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# ——— Enums (в коді), у БД зберігаємо як строки для простоти ———
class UserStatus(str, PyEnum):
    inactive = "inactive"
    active = "active"


class TaskStatus(str, PyEnum):
    pending = "pending"
    completed = "completed"


class WithdrawStatus(str, PyEnum):
    pending = "pending"
    processed = "processed"
    paid = "paid"


# ——— Таблиці ———
class Users(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    lang: Mapped[str | None]
    # зберігаємо статус як звичайний String у БД, у Python працюємо з рядком/enum як хочеш
    status: Mapped[str] = mapped_column(String(16), default="inactive", index=True)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    wallet: Mapped["QCWallets"] = relationship(back_populates="user", uselist=False)


class Payments(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_usd: Mapped[float]
    status: Mapped[str] = mapped_column(String(32), index=True)  # created/paid/partial/overpaid/canceled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Tasks(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ВАЖЛИВО: у Mapped ставимо Python-тип str, а SQL-тип передаємо в mapped_column(Text)
    title_uk: Mapped[str] = mapped_column(String)
    title_ru: Mapped[str] = mapped_column(String)
    title_en: Mapped[str] = mapped_column(String)

    desc_uk: Mapped[str] = mapped_column(Text)
    desc_ru: Mapped[str] = mapped_column(Text)
    desc_en: Mapped[str] = mapped_column(Text)

    url: Mapped[str] = mapped_column(String)
    reward_qc: Mapped[int] = mapped_column(default=1)
    chain_key: Mapped[str | None] = mapped_column(String(64), index=True)
    cooldown_sec: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)


class UserTasks(Base):
    __tablename__ = "user_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_user_task"),)


class QCWallets(Base):
    __tablename__ = "qc_wallets"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    balance_qc: Mapped[int] = mapped_column(default=0)
    total_earned_qc: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user: Mapped["Users"] = relationship(back_populates="wallet")


class Referrals(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    referee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Withdrawals(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_qc: Mapped[int]
    country: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String)
    details: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

# Індекси (додаткові комбіновані)
Index("ix_tasks_active_chain", Tasks.is_active, Tasks.chain_key)
Index("ix_user_tasks_user_status", UserTasks.user_id, UserTasks.status)
