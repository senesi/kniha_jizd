import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

SCHEMA = "core"


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list["UserRole"]] = relationship(back_populates="user")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.roles.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.permissions.id", ondelete="CASCADE"), nullable=False
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.roles.id", ondelete="CASCADE"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="roles")
    role: Mapped["Role"] = relationship()


class AuditLog(Base):
    """Append-only history of every important change (zadání 26 "Historie",
    30 "auditní log"). Never updated, never deleted - a correction to an
    already-closed trip adds a row here, it does not rewrite one."""

    __tablename__ = "audit_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # create/update/delete/login/...
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User | None"] = relationship()


class AppSetting(Base):
    """Admin-configurable application settings (zadání 7/19/20: warning
    thresholds must be configurable). Plain key/value text rows read
    through app/core/settings.py's typed accessors, deliberately not a
    one-column-per-setting table - adding a threshold must not need a
    migration."""

    __tablename__ = "app_settings"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )


class UserNotificationPreference(Base):
    """Volba jednoho uživatele u jednoho typu notifikace (zadání 20).

    **Ukládají se jen odchylky, ne kompletní tabulka.** Chybějící řádek
    znamená „výchozí hodnota podle katalogu"
    (`app/core/notification_types.py`), ne „vypnuto".

    Důvod je ten, že nový typ notifikace nemá vyžadovat migraci ani
    dávkový přepočet přes všechny uživatele: jakmile přibude v katalogu,
    platí pro každého jeho výchozí hodnota a dosavadní volby zůstanou
    nedotčené. Zároveň se tím nemůže stát, že uživatel založený nějakou
    jinou cestou (import, skript) zůstane bez řádků a přijde o všechno.

    Volba je vždycky jen tohoto uživatele - viz ROZHODNUTI.md R38.
    """

    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "notification_type", name="uq_core_user_notification_pref"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Kód z app/core/notification_types.py:TYPES.
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()
