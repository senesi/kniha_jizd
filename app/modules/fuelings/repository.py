import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Trip, TripFueling


def _load_options():
    return (
        selectinload(TripFueling.receipts),
        selectinload(TripFueling.trip).selectinload(Trip.vehicle),
    )


async def get(db: AsyncSession, fueling_id: uuid.UUID) -> TripFueling | None:
    result = await db.execute(
        select(TripFueling).where(TripFueling.id == fueling_id).options(*_load_options())
    )
    return result.scalar_one_or_none()


async def list_for_trip(db: AsyncSession, trip_id: uuid.UUID) -> list[TripFueling]:
    result = await db.execute(
        select(TripFueling)
        .where(TripFueling.trip_id == trip_id)
        .options(*_load_options())
        .order_by(TripFueling.fueled_at, TripFueling.created_at)
    )
    return list(result.scalars().all())


async def list_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, limit: int = 30) -> list[TripFueling]:
    result = await db.execute(
        select(TripFueling)
        .where(TripFueling.vehicle_id == vehicle_id)
        .options(*_load_options())
        .order_by(TripFueling.fueled_at.desc(), TripFueling.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def totals_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> list[dict]:
    """Součty po jednotkách - u plug-in hybridu jsou litry i kWh zvlášť,
    sčítat je dohromady by nedávalo smysl."""
    result = await db.execute(
        select(
            TripFueling.unit,
            func.sum(TripFueling.quantity),
            func.sum(TripFueling.price_total_czk),
            func.count(TripFueling.id),
        )
        .where(TripFueling.vehicle_id == vehicle_id)
        .group_by(TripFueling.unit)
        .order_by(TripFueling.unit)
    )
    return [
        {"unit": unit, "quantity": quantity, "price": price, "count": count}
        for unit, quantity, price, count in result.all()
    ]
