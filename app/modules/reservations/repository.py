import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Trip, VehicleReservation


def _load_options():
    return (
        selectinload(VehicleReservation.vehicle),
        selectinload(VehicleReservation.user),
    )


async def get(db: AsyncSession, reservation_id: uuid.UUID) -> VehicleReservation | None:
    result = await db.execute(
        select(VehicleReservation).where(VehicleReservation.id == reservation_id).options(*_load_options())
    )
    return result.scalar_one_or_none()


async def list_in_window(
    db: AsyncSession, *, start: datetime, end: datetime, vehicle_id: uuid.UUID | None = None,
) -> list[VehicleReservation]:
    """Vše, co v zadaném okně aspoň částečně leží - tedy i rezervace, která
    začala před jeho začátkem a pokračuje dovnitř. Porovnání `start < end
    AND end > start` je jediný správný test překryvu; porovnávat jen
    začátek by ořízlo vícedenní rezervace."""
    stmt = (
        select(VehicleReservation)
        .where(
            VehicleReservation.status == "active",
            VehicleReservation.start_at < end,
            VehicleReservation.end_at > start,
        )
        .options(*_load_options())
        .order_by(VehicleReservation.start_at)
    )
    if vehicle_id is not None:
        stmt = stmt.where(VehicleReservation.vehicle_id == vehicle_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, include_past: bool = False, limit: int = 100,
) -> list[VehicleReservation]:
    stmt = (
        select(VehicleReservation)
        .where(VehicleReservation.user_id == user_id)
        .options(*_load_options())
        .order_by(VehicleReservation.start_at.desc())
        .limit(limit)
    )
    if not include_past:
        stmt = stmt.where(VehicleReservation.end_at >= datetime.now(tz=None).astimezone())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def find_covering(
    db: AsyncSession, *, vehicle_id: uuid.UUID, moment: datetime,
) -> VehicleReservation | None:
    """Aktivní rezervace nebo servisní blok, do kterého daný okamžik spadá.

    Tohle je to, co při zahájení výpůjčky rozhoduje, jestli řidič uvidí
    varování (zadání 10)."""
    result = await db.execute(
        select(VehicleReservation)
        .where(
            VehicleReservation.vehicle_id == vehicle_id,
            VehicleReservation.status == "active",
            VehicleReservation.start_at <= moment,
            VehicleReservation.end_at > moment,
        )
        .options(*_load_options())
        .order_by(VehicleReservation.start_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_upcoming(db: AsyncSession, *, limit: int = 20) -> list[VehicleReservation]:
    result = await db.execute(
        select(VehicleReservation)
        .where(
            VehicleReservation.status == "active",
            VehicleReservation.end_at >= datetime.now(tz=None).astimezone(),
        )
        .options(*_load_options())
        .order_by(VehicleReservation.start_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def active_trips_in_window(
    db: AsyncSession, *, start: datetime, end: datetime,
) -> list[Trip]:
    """Probíhající výpůjčky, které kalendář kreslí jinak než rezervace -
    rezervace je nárok na termín, probíhající jízda je fakt."""
    result = await db.execute(
        select(Trip)
        .where(
            Trip.status == "active",
            Trip.started_at < end,
            or_(Trip.ended_at.is_(None), Trip.ended_at > start),
        )
        .options(selectinload(Trip.vehicle), selectinload(Trip.primary_driver))
    )
    return list(result.scalars().all())
