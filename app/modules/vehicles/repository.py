import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.core import User
from app.models.fleet import Attachment, Vehicle, VehicleAssignment


def _vehicle_load_options():
    return (
        selectinload(Vehicle.responsible_user),
        selectinload(Vehicle.photos),
    )


async def list_vehicles(
    db: AsyncSession, *, active_only: bool = False, visible_to=None,
) -> list[Vehicle]:
    """`visible_to` je podmínka z app/core/access.py:visible_vehicles_condition.
    None znamená „uživatel vidí všechno" - ne „nefiltrovat, protože jsme
    zapomněli". Každé volání ji má předat (požadavek D)."""
    stmt = select(Vehicle).where(Vehicle.deleted_at.is_(None))
    if active_only:
        stmt = stmt.where(Vehicle.is_active.is_(True))
    if visible_to is not None:
        stmt = stmt.where(visible_to)
    result = await db.execute(stmt.options(*_vehicle_load_options()).order_by(Vehicle.internal_code))
    return list(result.scalars().all())


async def list_vehicles_for_responsible_user(db: AsyncSession, user_id: uuid.UUID) -> list[Vehicle]:
    result = await db.execute(
        select(Vehicle)
        .where(Vehicle.deleted_at.is_(None), Vehicle.responsible_user_id == user_id)
        .options(*_vehicle_load_options())
        .order_by(Vehicle.internal_code)
    )
    return list(result.scalars().all())


async def get_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> Vehicle | None:
    result = await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.deleted_at.is_(None)).options(*_vehicle_load_options())
    )
    return result.scalar_one_or_none()


async def get_vehicle_by_qr_token(db: AsyncSession, qr_token: str) -> Vehicle | None:
    result = await db.execute(
        select(Vehicle)
        .where(Vehicle.qr_token == qr_token, Vehicle.deleted_at.is_(None))
        .options(*_vehicle_load_options())
    )
    return result.scalar_one_or_none()


async def qr_token_exists(db: AsyncSession, qr_token: str) -> bool:
    result = await db.execute(select(Vehicle.id).where(Vehicle.qr_token == qr_token))
    return result.scalar_one_or_none() is not None


async def internal_code_exists(db: AsyncSession, internal_code: str, *, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(Vehicle.id).where(Vehicle.internal_code == internal_code)
    if exclude_id is not None:
        stmt = stmt.where(Vehicle.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def list_active_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    return list(result.scalars().all())


async def list_assignments(db: AsyncSession, vehicle_id: uuid.UUID) -> list[VehicleAssignment]:
    result = await db.execute(
        select(VehicleAssignment)
        .where(VehicleAssignment.vehicle_id == vehicle_id)
        .options(selectinload(VehicleAssignment.user))
        .order_by(VehicleAssignment.valid_from.desc())
    )
    return list(result.scalars().all())


async def get_attachment(db: AsyncSession, attachment_id: uuid.UUID) -> Attachment | None:
    result = await db.execute(
        select(Attachment).where(Attachment.id == attachment_id, Attachment.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def list_attachments(
    db: AsyncSession, vehicle_id: uuid.UUID, *, kind: str | None = None
) -> list[Attachment]:
    stmt = select(Attachment).where(Attachment.vehicle_id == vehicle_id, Attachment.deleted_at.is_(None))
    if kind is not None:
        stmt = stmt.where(Attachment.kind == kind)
    result = await db.execute(stmt.order_by(Attachment.created_at))
    return list(result.scalars().all())
