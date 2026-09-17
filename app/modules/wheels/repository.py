import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Vehicle, WheelFitment, WheelSet


def _set_options():
    # Fotky i historie se vykreslují v šabloně, takže musí jít s sebou
    # (ROZHODNUTI.md R21).
    return (
        selectinload(WheelSet.photos),
        selectinload(WheelSet.fitments),
        selectinload(WheelSet.vehicle).selectinload(Vehicle.photos),
    )


async def get_set(db: AsyncSession, wheel_set_id: uuid.UUID) -> WheelSet | None:
    result = await db.execute(
        select(WheelSet)
        .where(WheelSet.id == wheel_set_id, WheelSet.deleted_at.is_(None))
        .options(*_set_options())
    )
    return result.scalar_one_or_none()


async def list_sets(db: AsyncSession, vehicle_id: uuid.UUID) -> list[WheelSet]:
    result = await db.execute(
        select(WheelSet)
        .where(WheelSet.vehicle_id == vehicle_id, WheelSet.deleted_at.is_(None))
        .options(*_set_options())
        .order_by(WheelSet.season, WheelSet.purchased_at.desc())
    )
    return list(result.scalars().all())


async def get_active_fitment(db: AsyncSession, vehicle_id: uuid.UUID) -> WheelFitment | None:
    """Co je na vozidle právě nasazené. Jediný zdroj pravdy - nikde se to
    neukládá jako příznak."""
    result = await db.execute(
        select(WheelFitment)
        .where(WheelFitment.vehicle_id == vehicle_id, WheelFitment.removed_at.is_(None))
        .options(
            # Fotky i všechna období sady: karta vozidla z nich počítá
            # nájezd, takže se musí načíst dopředu (ROZHODNUTI.md R21).
            selectinload(WheelFitment.wheel_set).selectinload(WheelSet.photos),
            selectinload(WheelFitment.wheel_set).selectinload(WheelSet.fitments),
        )
    )
    return result.scalar_one_or_none()


async def get_active_fitment_for_set(db: AsyncSession, wheel_set_id: uuid.UUID) -> WheelFitment | None:
    result = await db.execute(
        select(WheelFitment)
        .where(WheelFitment.wheel_set_id == wheel_set_id, WheelFitment.removed_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_fitment(db: AsyncSession, fitment_id: uuid.UUID) -> WheelFitment | None:
    result = await db.execute(
        select(WheelFitment)
        .where(WheelFitment.id == fitment_id)
        .options(
            selectinload(WheelFitment.wheel_set).selectinload(WheelSet.photos),
            selectinload(WheelFitment.wheel_set).selectinload(WheelSet.fitments),
        )
    )
    return result.scalar_one_or_none()


async def list_fitments(db: AsyncSession, vehicle_id: uuid.UUID) -> list[WheelFitment]:
    """Historie přezutí od nejnovějšího."""
    result = await db.execute(
        select(WheelFitment)
        .where(WheelFitment.vehicle_id == vehicle_id)
        .options(selectinload(WheelFitment.wheel_set))
        .order_by(WheelFitment.fitted_at.desc(), WheelFitment.created_at.desc())
    )
    return list(result.scalars().all())
