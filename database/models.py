from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator


class Base(DeclarativeBase):
    pass


class JsonType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class UserRole(str, Enum):
    employee = "employee"
    admin = "admin"


class DealStatus(str, Enum):
    pending = "pending"
    success = "success"
    error = "error"


class PayoutStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"


class TaskStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class MappingMixin:
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class User(MappingMixin, Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    referrer_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 9), default=Decimal("0"), nullable=False)
    earned: Mapped[Decimal] = mapped_column(Numeric(18, 9), default=Decimal("0"), nullable=False)
    tag: Mapped[str] = mapped_column(String(32), default="Аноним", nullable=False)
    mentor_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    payout_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("70"), nullable=False)
    wallet: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Новый", nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=UserRole.employee.value, nullable=False)
    is_employee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_main_chat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)

    commission_rates: Mapped[list[CommissionRate]] = relationship(back_populates="user")
    payout_requests: Mapped[list[PayoutRequest]] = relationship(back_populates="user")
    valuations: Mapped[list[NFTValuation]] = relationship(back_populates="user")


class CommissionRate(MappingMixin, Base):
    __tablename__ = "commission_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True, nullable=False)
    payout_rate: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    mentor_rate: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="commission_rates")


class PayoutRequest(MappingMixin, Base):
    __tablename__ = "payout_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=PayoutStatus.pending.value, index=True)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="payout_requests")

    @property
    def username(self) -> Optional[str]:
        return self.user.username if self.user else None

    @property
    def first_name(self) -> Optional[str]:
        return self.user.first_name if self.user else None


class TransactionLog(MappingMixin, Base):
    __tablename__ = "transaction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 9), nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)


class NFTValuation(MappingMixin, Base):
    __tablename__ = "nft_valuations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    collection: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    item_address: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    floor_price: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    payout_rate: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    employee_share: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    credited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="valuations")


class Setting(MappingMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Mentor(MappingMixin, Base):
    __tablename__ = "mentors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(64), default="Наставник", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PayoutFeed(MappingMixin, Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Earning(MappingMixin, Base):
    __tablename__ = "earnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Deal(MappingMixin, Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=DealStatus.pending.value, index=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 9), nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Task(MappingMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assigned_to: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.open.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TaskReport(MappingMixin, Base):
    __tablename__ = "task_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    deal_number: Mapped[str] = mapped_column(String(64), nullable=False)
    file_ids: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 9), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DepositTicket(MappingMixin, Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
