import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Vehicle, VehicleService


def _load_options():
    return (
        selectinload(VehicleService.vehicle).selectinload(Vehicle.photos),
        selectinload(VehicleService.attachments),
    )


async def get(db: AsyncSession, service_id: uuid.UUID) -> VehicleService | None:
    result = await db.execute(
        select(VehicleService)
        .where(VehicleService.id == service_id, VehicleService.deleted_at.is_(None))
        .options(*_load_options())
    )
    return result.scalar_one_or_none()


async def list_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> list[VehicleService]:
    result = await db.execute(
        select(VehicleService)
        .where(VehicleService.vehicle_id == vehicle_id, VehicleService.deleted_at.is_(None))
        .options(*_load_options())
        .order_by(VehicleService.service_date.desc(), VehicleService.created_at.desc())
    )
    return list(result.scalars().all())


async def totals_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> dict:
    """Počet úkonů a celková cena. Cena je nepovinná, takže součet může
    být None - to není nula, ale „nevíme"."""
    result = await db.execute(
        select(func.count(VehicleService.id), func.sum(VehicleService.price_czk))
        .where(VehicleService.vehicle_id == vehicle_id, VehicleService.deleted_at.is_(None))
    )
    count, price = result.one()
    return {"count": count, "price": price}


async def last_of_type(db: AsyncSession, vehicle_id: uuid.UUID, service_type: str) -> VehicleService | None:
    result = await db.execute(
        select(VehicleService)
        .where(
            VehicleService.vehicle_id == vehicle_id,
            VehicleService.service_types.any(service_type),
            VehicleService.deleted_at.is_(None),
        )
        .options(*_load_options())
        .order_by(VehicleService.service_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
