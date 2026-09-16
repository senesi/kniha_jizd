import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Vehicle, VehicleDefect

# Pořadí, ve kterém mají závady chodit na oči: kritické nahoru.
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def _load_options():
    # Vozidlo i s fotkami - seznam závad ukazuje náhled vozidla (C).
    return (
        selectinload(VehicleDefect.vehicle).selectinload(Vehicle.photos),
        selectinload(VehicleDefect.reporter),
        selectinload(VehicleDefect.photos),
    )


async def get(db: AsyncSession, defect_id: uuid.UUID) -> VehicleDefect | None:
    result = await db.execute(
        select(VehicleDefect).where(VehicleDefect.id == defect_id).options(*_load_options())
    )
    return result.scalar_one_or_none()


def _sorted(defects: list[VehicleDefect]) -> list[VehicleDefect]:
    """Řazení podle priority a pak od nejnovější. Dělá se v Pythonu, ne v
    SQL: priorita je textový kód, takže ORDER BY by řadil abecedně
    („critical" před „normal" jen náhodou, „high" před „low" ne)."""
    return sorted(
        defects,
        key=lambda defect: (PRIORITY_ORDER.get(defect.priority, 9), -defect.reported_at.timestamp()),
    )


async def list_for_vehicle(
    db: AsyncSession, vehicle_id: uuid.UUID, *, open_only: bool = False,
) -> list[VehicleDefect]:
    stmt = select(VehicleDefect).where(VehicleDefect.vehicle_id == vehicle_id).options(*_load_options())
    if open_only:
        stmt = stmt.where(VehicleDefect.status != "resolved")
    result = await db.execute(stmt)
    return _sorted(list(result.scalars().all()))


async def list_open_for_vehicles(db: AsyncSession, vehicle_ids: list[uuid.UUID]) -> dict:
    """Otevřené závady pro víc vozidel najednou - jedním dotazem, aby
    seznam vozidel nedělal N+1."""
    if not vehicle_ids:
        return {}
    result = await db.execute(
        select(VehicleDefect)
        .where(VehicleDefect.vehicle_id.in_(vehicle_ids), VehicleDefect.status != "resolved")
        .options(*_load_options())
    )
    grouped: dict = {}
    for defect in result.scalars().all():
        grouped.setdefault(defect.vehicle_id, []).append(defect)
    return {vehicle_id: _sorted(items) for vehicle_id, items in grouped.items()}


async def list_all(
    db: AsyncSession, *, status: str = "", priority: str = "", visible_to=None,
) -> list[VehicleDefect]:
    stmt = (
        select(VehicleDefect)
        .join(Vehicle, Vehicle.id == VehicleDefect.vehicle_id)
        .options(*_load_options())
    )
    if status == "open":
        stmt = stmt.where(VehicleDefect.status != "resolved")
    elif status:
        stmt = stmt.where(VehicleDefect.status == status)
    if priority:
        stmt = stmt.where(VehicleDefect.priority == priority)
    if visible_to is not None:
        stmt = stmt.where(visible_to)
    result = await db.execute(stmt)
    return _sorted(list(result.scalars().all()))


async def count_open(db: AsyncSession, *, critical_only: bool = False, visible_to=None) -> int:
    stmt = (
        select(VehicleDefect.id)
        .join(Vehicle, Vehicle.id == VehicleDefect.vehicle_id)
        .where(VehicleDefect.status != "resolved")
    )
    if critical_only:
        stmt = stmt.where(VehicleDefect.priority == "critical")
    if visible_to is not None:
        stmt = stmt.where(visible_to)
    result = await db.execute(stmt)
    return len(result.all())


async def list_for_trip(db: AsyncSession, trip_id: uuid.UUID) -> list[VehicleDefect]:
    result = await db.execute(
        select(VehicleDefect).where(VehicleDefect.trip_id == trip_id).options(*_load_options())
    )
    return _sorted(list(result.scalars().all()))


async def list_reported_by(db: AsyncSession, user_id: uuid.UUID, *, limit: int = 50) -> list[VehicleDefect]:
    result = await db.execute(
        select(VehicleDefect)
        .where(or_(VehicleDefect.reported_by == user_id, VehicleDefect.resolved_by == user_id))
        .options(*_load_options())
        .order_by(VehicleDefect.reported_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
