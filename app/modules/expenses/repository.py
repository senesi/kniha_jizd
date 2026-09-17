import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Trip, TripFueling, Vehicle, VehicleExpense


def _load_options():
    return (
        selectinload(VehicleExpense.vehicle).selectinload(Vehicle.photos),
        selectinload(VehicleExpense.creator),
        selectinload(VehicleExpense.receipts),
        selectinload(VehicleExpense.trip),
    )


async def get(db: AsyncSession, expense_id: uuid.UUID) -> VehicleExpense | None:
    result = await db.execute(
        select(VehicleExpense)
        .where(VehicleExpense.id == expense_id, VehicleExpense.deleted_at.is_(None))
        .options(*_load_options())
    )
    return result.scalar_one_or_none()


def _window(stmt, column, date_from: date | None, date_to: date | None):
    if date_from is not None:
        stmt = stmt.where(column >= date_from)
    if date_to is not None:
        stmt = stmt.where(column <= date_to)
    return stmt


async def list_for_vehicle(
    db: AsyncSession, vehicle_id: uuid.UUID, *, date_from: date | None = None,
    date_to: date | None = None, expense_type: str = "", limit: int = 200,
) -> list[VehicleExpense]:
    stmt = (
        select(VehicleExpense)
        .where(VehicleExpense.vehicle_id == vehicle_id, VehicleExpense.deleted_at.is_(None))
        .options(*_load_options())
        .order_by(VehicleExpense.expense_date.desc(), VehicleExpense.created_at.desc())
        .limit(limit)
    )
    stmt = _window(stmt, VehicleExpense.expense_date, date_from, date_to)
    if expense_type:
        stmt = stmt.where(VehicleExpense.expense_type == expense_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_for_trip(db: AsyncSession, trip_id: uuid.UUID) -> list[VehicleExpense]:
    result = await db.execute(
        select(VehicleExpense)
        .where(VehicleExpense.trip_id == trip_id, VehicleExpense.deleted_at.is_(None))
        .options(*_load_options())
        .order_by(VehicleExpense.expense_date)
    )
    return list(result.scalars().all())


async def totals_by_type(
    db: AsyncSession, vehicle_id: uuid.UUID, *, date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    stmt = (
        select(VehicleExpense.expense_type, func.sum(VehicleExpense.amount_czk), func.count(VehicleExpense.id))
        .where(VehicleExpense.vehicle_id == vehicle_id, VehicleExpense.deleted_at.is_(None))
        .group_by(VehicleExpense.expense_type)
    )
    stmt = _window(stmt, VehicleExpense.expense_date, date_from, date_to)
    result = await db.execute(stmt)
    return [
        {"expense_type": kind, "amount": amount, "count": count}
        for kind, amount, count in result.all()
    ]


async def fueling_total(
    db: AsyncSession, vehicle_id: uuid.UUID, *, date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Útrata za tankování a nabíjení z jízd.

    Tankování se do výdajů nepřepisuje (ROZHODNUTI.md R27) - aby ale
    celkový přehled nákladů nelhal, načítá se odsud jako druhý zdroj.
    """
    stmt = (
        select(func.sum(TripFueling.price_total_czk), func.count(TripFueling.id))
        .where(TripFueling.vehicle_id == vehicle_id)
    )
    stmt = _window(stmt, TripFueling.fueled_at, date_from, date_to)
    amount, count = (await db.execute(stmt)).one()
    return {"amount": amount, "count": count}


async def combined_totals(
    db: AsyncSession, vehicle_id: uuid.UUID, *, date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Celkové náklady = zapsané výdaje + tankování z jízd."""
    by_type = await totals_by_type(db, vehicle_id, date_from=date_from, date_to=date_to)
    fuel = await fueling_total(db, vehicle_id, date_from=date_from, date_to=date_to)

    expenses_sum = sum(float(row["amount"] or 0) for row in by_type)
    fuel_sum = float(fuel["amount"] or 0)
    return {
        "by_type": sorted(by_type, key=lambda row: -float(row["amount"] or 0)),
        "expenses_amount": expenses_sum,
        "fuelings_amount": fuel_sum,
        "fuelings_count": fuel["count"],
        "total": expenses_sum + fuel_sum,
    }


async def list_trips_for_picker(db: AsyncSession, vehicle_id: uuid.UUID, *, limit: int = 20) -> list[Trip]:
    """Poslední jízdy vozidla pro nepovinné navázání výdaje."""
    result = await db.execute(
        select(Trip)
        .where(Trip.vehicle_id == vehicle_id, Trip.status != "cancelled")
        .order_by(Trip.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
