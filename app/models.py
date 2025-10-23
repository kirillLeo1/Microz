from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, Index, Enum, Integer
from datetime import datetime
from enum import Enum as PyEnum
from .db import Base

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

class Users(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    lang: Mapped[str | None]
    status: Mapped[UserStatus] = mapped_column(default=UserStatus.inactive, index=True)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    wallet = relationship("QCWallets", uselist=False, back_populates="user")

class Payments(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_usd: Mapped[float]
    status: Mapped[str] = mapped_column(String(32), index=True)  # created/paid/partial/overpaid/canceled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Tasks(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title_uk: Mapped[str]
    title_ru: Mapped[str]
    title_en: Mapped[str]
    desc_uk: Mapped[Text]
    desc_ru: Mapped[Text]
    desc_en: Mapped[Text]
    url: Mapped[str]
    reward_qc: Mapped[int] = mapped_column(default=1)
    chain_key: Mapped[str | None] = mapped_column(String(64), index=True)
    cooldown_sec: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

class UserTasks(Base):
    __tablename__ = "user_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    status: Mapped[TaskStatus] = mapped_column(default=TaskStatus.pending, index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_user_task"),)

class QCWallets(Base):
    __tablename__ = "qc_wallets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    balance_qc: Mapped[int] = mapped_column(default=0)
    total_earned_qc: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user = relationship("Users", back_populates="wallet")

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
    country: Mapped[str]
    method: Mapped[str]
    details: Mapped[str | None]
    status: Mapped[WithdrawStatus] = mapped_column(default=WithdrawStatus.pending, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class KV(Base):
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]

Index("ix_tasks_active_chain", Tasks.is_active, Tasks.chain_key)
Index("ix_user_tasks_user_status", UserTasks.user_id, UserTasks.status)