import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import TripRequest, Vehicle


def _load_options():
    # Vozidlo se načítá včetně fotek: seznam žádostí vykresluje náhled
    # vozidla (požadavek C) a bez eager loadingu by se galerie dotahovala
    # až v šabloně - tedy mimo async kontext, což na async session skončí
    # chybou MissingGreenlet.
    return (
        selectinload(TripRequest.vehicle).selectinload(Vehicle.photos),
        selectinload(TripRequest.requester),
        selectinload(TripRequest.decider),
    )


async def get(db: AsyncSession, request_id: uuid.UUID) -> TripRequest | None:
    result = await db.execute(
        select(TripRequest).where(TripRequest.id == request_id).options(*_load_options())
    )
    return result.scalar_one_or_none()


async def get_pending_for(
    db: AsyncSession, *, vehicle_id: uuid.UUID, requester_id: uuid.UUID,
) -> TripRequest | None:
    result = await db.execute(
        select(TripRequest)
        .where(
            TripRequest.vehicle_id == vehicle_id,
            TripRequest.requester_id == requester_id,
            TripRequest.status == "pending",
        )
        .options(*_load_options())
    )
    return result.scalar_one_or_none()


async def get_usable_approval(
    db: AsyncSession, *, vehicle_id: uuid.UUID, requester_id: uuid.UUID,
) -> TripRequest | None:
    """Platné, dosud nepoužité schválení. „Nepoužité" znamená, že na něj
    neukazuje žádná jízda - Trip.request_id je jediný záznam o použití."""
    from app.models.fleet import Trip

    used = select(Trip.request_id).where(Trip.request_id.is_not(None))
    result = await db.execute(
        select(TripRequest)
        .where(
            TripRequest.vehicle_id == vehicle_id,
            TripRequest.requester_id == requester_id,
            TripRequest.status == "approved",
            TripRequest.valid_until > datetime.now(timezone.utc),
            TripRequest.id.not_in(used),
        )
        .options(*_load_options())
        .order_by(TripRequest.decided_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_for_requester(db: AsyncSession, requester_id: uuid.UUID, *, limit: int = 50) -> list[TripRequest]:
    result = await db.execute(
        select(TripRequest)
        .where(TripRequest.requester_id == requester_id)
        .options(*_load_options())
        .order_by(TripRequest.requested_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_pending_for_decider(
    db: AsyncSession, *, user_id: uuid.UUID, sees_all: bool,
) -> list[TripRequest]:
    """Žádosti čekající na rozhodnutí. Administrátor vidí všechny,
    odpovědná osoba jen ty na svá vozidla."""
    stmt = (
        select(TripRequest)
        .join(Vehicle, Vehicle.id == TripRequest.vehicle_id)
        .where(TripRequest.status == "pending")
        .options(*_load_options())
        .order_by(TripRequest.requested_at)
    )
    if not sees_all:
        stmt = stmt.where(Vehicle.responsible_user_id == user_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_pending_for_decider(db: AsyncSession, *, user_id: uuid.UUID, sees_all: bool) -> int:
    rows = await list_pending_for_decider(db, user_id=user_id, sees_all=sees_all)
    return len(rows)


async def list_open_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> list[TripRequest]:
    result = await db.execute(
        select(TripRequest)
        .where(
            TripRequest.vehicle_id == vehicle_id,
            or_(TripRequest.status == "pending", TripRequest.status == "approved"),
        )
        .options(*_load_options())
        .order_by(TripRequest.requested_at.desc())
    )
    return list(result.scalars().all())
