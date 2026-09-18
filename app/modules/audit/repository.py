"""Dotazy nad auditním logem (zadání 26/30).

**Jen čtení.** Modul záměrně neobsahuje žádnou funkci, která by auditní
řádek měnila nebo mazala — append-only není vlastnost obrazovky, ale
toho, že tudy nevede cesta zpátky.
"""
import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.core import AuditLog, User
from app.models.fleet import Vehicle
from app.modules.audit.filters import AUTH_MODULE, PAGE_SIZE, AuditFilter


def _apply(stmt: Select, flt: AuditFilter) -> Select:
    if flt.created_from is not None:
        stmt = stmt.where(AuditLog.created_at >= flt.created_from)
    if flt.created_to is not None:
        stmt = stmt.where(AuditLog.created_at < flt.created_to)
    if flt.user_id is not None:
        stmt = stmt.where(AuditLog.user_id == flt.user_id)
    if flt.vehicle_id is not None:
        stmt = stmt.where(AuditLog.vehicle_id == flt.vehicle_id)
    if flt.module is not None:
        stmt = stmt.where(AuditLog.module == flt.module)
    if flt.action is not None:
        stmt = stmt.where(AuditLog.action == flt.action)
    if flt.result is not None:
        stmt = stmt.where(AuditLog.result == flt.result)

    if flt.event_kind == "logins":
        stmt = stmt.where(AuditLog.module == AUTH_MODULE)
    elif flt.event_kind == "changes":
        stmt = stmt.where(AuditLog.module != AUTH_MODULE)

    if flt.search:
        # Hledá se v tom, co je čitelné: popis, typ objektu a jeho id.
        # Ne v JSON payloadu - tam by to bez indexu bylo pomalé a
        # výsledky matoucí.
        needle = f"%{flt.search}%"
        stmt = stmt.where(or_(
            AuditLog.description.ilike(needle),
            AuditLog.entity_type.ilike(needle),
            AuditLog.entity_id.ilike(needle),
        ))
    return stmt


async def list_entries(db: AsyncSession, flt: AuditFilter) -> list[AuditLog]:
    stmt = _apply(select(AuditLog), flt).options(
        selectinload(AuditLog.user),
    ).order_by(
        # Od nejnovějších; id jako druhé kritérium, aby bylo pořadí
        # stabilní u záznamů se shodným časem (jedna akce jich zapíše víc).
        AuditLog.created_at.desc(), AuditLog.id.desc(),
    ).limit(PAGE_SIZE).offset(flt.offset)
    return list((await db.execute(stmt)).scalars().all())


async def count_entries(db: AsyncSession, flt: AuditFilter) -> int:
    return int((await db.execute(_apply(select(func.count(AuditLog.id)), flt))).scalar_one())


async def get(db: AsyncSession, entry_id: uuid.UUID) -> AuditLog | None:
    return (await db.execute(
        select(AuditLog).where(AuditLog.id == entry_id).options(selectinload(AuditLog.user))
    )).scalar_one_or_none()


async def vehicles_for(db: AsyncSession, entries) -> dict:
    """Vozidla zmíněná ve výpisu, jedním dotazem.

    Vztah `AuditLog.vehicle` schválně není: audit nemá držet ORM vazbu na
    doménový model kvůli jednomu popisku. Tohle je jen doplnění popisků
    pro zobrazení."""
    ids = {entry.vehicle_id for entry in entries if entry.vehicle_id}
    if not ids:
        return {}
    rows = (await db.execute(select(Vehicle).where(Vehicle.id.in_(ids)))).scalars().all()
    return {vehicle.id: vehicle for vehicle in rows}


# --- nabídky do filtru -------------------------------------------------

async def distinct_modules(db: AsyncSession) -> list[str]:
    rows = (await db.execute(
        select(AuditLog.module).distinct().order_by(AuditLog.module)
    )).scalars().all()
    return list(rows)


async def distinct_actions(db: AsyncSession) -> list[str]:
    rows = (await db.execute(
        select(AuditLog.action).distinct().order_by(AuditLog.action)
    )).scalars().all()
    return list(rows)


async def actors(db: AsyncSession) -> list[User]:
    """Jen lidé, kteří v auditu opravdu figurují - nabízet celý seznam
    uživatelů by znamenalo dlouhé rolování bez užitku."""
    used = select(AuditLog.user_id).where(AuditLog.user_id.is_not(None)).distinct()
    return list((await db.execute(
        select(User).where(User.id.in_(used)).order_by(User.full_name)
    )).scalars().all())


async def audited_vehicles(db: AsyncSession) -> list[Vehicle]:
    used = select(AuditLog.vehicle_id).where(AuditLog.vehicle_id.is_not(None)).distinct()
    return list((await db.execute(
        select(Vehicle).where(Vehicle.id.in_(used)).order_by(Vehicle.license_plate)
    )).scalars().all())
