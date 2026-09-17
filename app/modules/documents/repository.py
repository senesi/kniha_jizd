import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Vehicle, VehicleDocument


def _load_options():
    return (
        selectinload(VehicleDocument.vehicle).selectinload(Vehicle.photos),
        selectinload(VehicleDocument.uploader),
    )


async def get(db: AsyncSession, document_id: uuid.UUID) -> VehicleDocument | None:
    """Soft-smazaný dokument se nenajde - tím je hned nedostupný i přes
    přímý odkaz."""
    result = await db.execute(
        select(VehicleDocument)
        .where(VehicleDocument.id == document_id, VehicleDocument.deleted_at.is_(None))
        .options(*_load_options())
    )
    return result.scalar_one_or_none()


async def list_for_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> list[VehicleDocument]:
    result = await db.execute(
        select(VehicleDocument)
        .where(VehicleDocument.vehicle_id == vehicle_id, VehicleDocument.deleted_at.is_(None))
        .options(*_load_options())
        .order_by(VehicleDocument.doc_type, VehicleDocument.created_at.desc())
    )
    return list(result.scalars().all())
