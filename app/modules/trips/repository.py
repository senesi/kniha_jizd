import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.core import User
from app.models.fleet import Trip, TripDriver, Vehicle


def _trip_load_options():
    return (
        selectinload(Trip.vehicle),
        selectinload(Trip.primary_driver),
        selectinload(Trip.extra_drivers).selectinload(TripDriver.user),
        selectinload(Trip.fuelings),
        selectinload(Trip.notes),
        selectinload(Trip.photos),
    )


async def get_trip(db: AsyncSession, trip_id: uuid.UUID) -> Trip | None:
    result = await db.execute(select(Trip).where(Trip.id == trip_id).options(*_trip_load_options()))
    return result.scalar_one_or_none()


async def get_active_trip_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> Trip | None:
    """Jestli je vozidlo právě vypůjčené, se pozná odsud - není to uložený
    příznak na vozidle, který by se mohl rozejít se skutečností."""
    result = await db.execute(
        select(Trip)
        .where(Trip.vehicle_id == vehicle_id, Trip.status == "active")
        .options(*_trip_load_options())
    )
    return result.scalar_one_or_none()


async def list_active_trips_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[Trip]:
    """Jízdy, které uživatel může zavřít - vlastní i ty, kde je vedený
    jako další řidič."""
    as_extra = select(TripDriver.trip_id).where(TripDriver.user_id == user_id)
    result = await db.execute(
        select(Trip)
        .where(Trip.status == "active", (Trip.primary_driver_id == user_id) | Trip.id.in_(as_extra))
        .options(*_trip_load_options())
        .order_by(Trip.started_at.desc())
    )
    return list(result.scalars().all())


async def list_trips_for_user(db: AsyncSession, user_id: uuid.UUID, *, limit: int = 100) -> list[Trip]:
    as_extra = select(TripDriver.trip_id).where(TripDriver.user_id == user_id)
    result = await db.execute(
        select(Trip)
        .where((Trip.primary_driver_id == user_id) | Trip.id.in_(as_extra))
        .options(*_trip_load_options())
        .order_by(Trip.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_trips_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, limit: int = 20) -> list[Trip]:
    result = await db.execute(
        select(Trip)
        .where(Trip.vehicle_id == vehicle_id)
        .options(*_trip_load_options())
        .order_by(Trip.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_last_completed_trip(db: AsyncSession, vehicle_id: uuid.UUID) -> Trip | None:
    result = await db.execute(
        select(Trip)
        .where(Trip.vehicle_id == vehicle_id, Trip.status == "completed")
        .options(*_trip_load_options())
        .order_by(Trip.ended_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_all_active_trips(db: AsyncSession) -> list[Trip]:
    result = await db.execute(
        select(Trip).where(Trip.status == "active").options(*_trip_load_options()).order_by(Trip.started_at)
    )
    return list(result.scalars().all())


async def count_active_trips(db: AsyncSession) -> int:
    result = await db.execute(select(Trip.id).where(Trip.status == "active"))
    return len(result.all())


async def list_candidate_drivers(db: AsyncSession, trip: Trip) -> list[User]:
    """Aktivní uživatelé, které lze k jízdě přidat jako další řidiče -
    bez primárního řidiče a bez těch, kdo už u jízdy jsou."""
    taken = {trip.primary_driver_id, *(assignment.user_id for assignment in trip.extra_drivers)}
    result = await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    return [candidate for candidate in result.scalars().all() if candidate.id not in taken]


async def vehicle_with_state(db: AsyncSession, vehicle_id: uuid.UUID) -> Vehicle | None:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.deleted_at.is_(None)))
    return result.scalar_one_or_none()
