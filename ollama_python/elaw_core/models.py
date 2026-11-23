"""Database tables. Secrets (passwords, tokens) are only ever stored as hashes."""

import enum

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from elaw_core.db import Base, utcnow


class Role(str, enum.Enum):
    USER = "USER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class Status(str, enum.Enum):
    PENDING = "PENDING"      # registered, email not verified yet
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"  # temporary block by an admin
    DISABLED = "DISABLED"    # account switched off (soft delete)


class Plan(str, enum.Enum):
    FREE = "FREE"
    PERSONAL = "PERSONAL"
    PRO = "PRO"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


ADMIN_ROLES = {Role.ADMIN, Role.SUPER_ADMIN}


def _enum(e, name):
    return Enum(e, name=name, native_enum=False, length=20, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[Status] = mapped_column(_enum(Status, "user_status"), default=Status.PENDING,
                                           index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), default=Role.USER, index=True)
    plan: Mapped[Plan] = mapped_column(_enum(Plan, "user_plan"), default=Plan.FREE)
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow,
                               nullable=False)
    last_login_at = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_activity_at = mapped_column(DateTime(timezone=True), nullable=True)

    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan",
                            passive_deletes=True)

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLES


class _TokenMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
    used_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EmailVerificationToken(_TokenMixin, Base):
    __tablename__ = "email_verification_tokens"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class PasswordResetToken(_TokenMixin, Base):
    __tablename__ = "password_reset_tokens"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")


class LoginEvent(Base):
    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_email_created", "email", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(254))
    login_success: Mapped[bool] = mapped_column(Boolean, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(30), index=True)
    feature: Mapped[str] = mapped_column(String(30), default="chat")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)  # USD
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
