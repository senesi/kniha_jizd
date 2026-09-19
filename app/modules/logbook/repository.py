"""Dotazy knihy jízd (zadání 22).

Jeden skládaný dotaz pro výpis, souhrn i export - viz `_base_query`.
Kdyby si každý z nich stavěl podmínky po svém, export by dřív nebo
později vracel jiná data než obrazovka.

**Viditelnost vozidla se řeší tady, ne v šabloně.** Skryté vozidlo pro
běžného uživatele neexistuje (požadavek D), takže jeho jízdy nesmí být
ani ve výpisu, ani v součtech, ani v exportu. Podmínka se přidává do
WHERE; vyfiltrovat to až při vykreslení by znamenalo, že v XLSX budou.
"""
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.access import visible_vehicles_condition
from app.models.core import User
from app.models.fleet import Trip, TripDriver, TripFueling, Vehicle
from app.modules.logbook.filters import PAGE_SIZE, LogbookFilter


def _load_options():
    """Co šablona i export procházejí cyklem, musí být načtené dopředu -
    jinak se to dotahuje až při renderu, mimo async kontext (R21)."""
    return (
        # Vnořená úroveň taky: výpis kreslí náhledovou fotku vozidla
        # makrem thumb(), které sahá na vehicle.photos (R21).
        selectinload(Trip.vehicle).selectinload(Vehicle.photos),
        selectinload(Trip.primary_driver),
        selectinload(Trip.extra_drivers).selectinload(TripDriver.user),
        selectinload(Trip.fuelings),
        selectinload(Trip.defects),
        selectinload(Trip.notes),
    )


def _base_query(select_stmt: Select, flt: LogbookFilter, *, codes: set[str], user: User) -> Select:
    stmt = select_stmt.join(Vehicle, Trip.vehicle_id == Vehicle.id)

    visible = visible_vehicles_condition(codes, user)
    if visible is not None:
        stmt = stmt.where(visible)

    if flt.started_from is not None:
        stmt = stmt.where(Trip.started_at >= flt.started_from)
    if flt.started_to is not None:
        stmt = stmt.where(Trip.started_at < flt.started_to)
    if flt.vehicle_id is not None:
        stmt = stmt.where(Trip.vehicle_id == flt.vehicle_id)
    if flt.purpose_code is not None:
        stmt = stmt.where(Trip.purpose_code == flt.purpose_code)
    if flt.status is not None:
        stmt = stmt.where(Trip.status == flt.status)

    if flt.driver_id is not None:
        # Řidič je buď primární, nebo zapsaný jako další - zadání 12 je
        # staví naroveň, takže filtr musí najít obojí. EXISTS, ne JOIN:
        # jízda se dvěma dalšími řidiči by se jinak ve výpisu zdvojila.
        stmt = stmt.where(
            (Trip.primary_driver_id == flt.driver_id)
            | select(TripDriver.id).where(
                TripDriver.trip_id == Trip.id,
                TripDriver.user_id == flt.driver_id,
            ).exists()
        )

    return stmt


async def list_trips(
    db: AsyncSession, flt: LogbookFilter, *, codes: set[str], user: User, paginate: bool = True,
) -> list[Trip]:
    stmt = _base_query(select(Trip), flt, codes=codes, user=user)
    stmt = stmt.options(*_load_options()).order_by(Trip.started_at.desc(), Trip.id)
    if paginate:
        stmt = stmt.limit(PAGE_SIZE).offset(flt.offset)
    return list((await db.execute(stmt)).scalars().all())


async def count_trips(db: AsyncSession, flt: LogbookFilter, *, codes: set[str], user: User) -> int:
    stmt = _base_query(select(func.count(Trip.id)), flt, codes=codes, user=user)
    return int((await db.execute(stmt)).scalar_one())


async def summary(db: AsyncSession, flt: LogbookFilter, *, codes: set[str], user: User) -> dict:
    """Součty za vyfiltrované období.

    Ujeté km se počítají jen z ukončených jízd - u probíhající není
    konečný stav tachometru, takže by se do součtu vešla jako nula a
    tiše ho podhodnotila."""
    driven = func.coalesce(
        func.sum(Trip.end_odometer_km - Trip.start_odometer_km).filter(
            Trip.end_odometer_km.is_not(None)
        ), 0,
    )
    stmt = _base_query(
        select(
            func.count(Trip.id),
            driven,
            func.count(Trip.id).filter(Trip.status == "active"),
        ),
        flt, codes=codes, user=user,
    )
    count, total_km, active = (await db.execute(stmt)).one()

    # Tankování se sčítá zvlášť: jedna jízda jich může mít víc a přes
    # JOIN by znásobila počet jízd i ujeté km.
    #
    # Počítá se výhradně tankování NAVÁZANÉ NA JÍZDU. Tankování mimo
    # jízdu (vozidla bez knihy jízd, nabíjení v depu) sem nepatří -
    # kniha jízd je o jízdách. Celkové palivo za vozidlo je v přehledu
    # vozidla, kde se sčítá podle vehicle_id a obojí zahrnuje.
    # select_from(Trip) je nutné: bez něj si SQLAlchemy odvodí FROM z
    # vybraných sloupců (tedy trip_fuelings) a JOIN na vozidla přes
    # trips.vehicle_id pak nemá na co navázat.
    fuel_stmt = _base_query(
        select(
            func.coalesce(func.sum(TripFueling.price_total_czk), 0),
            func.count(TripFueling.id),
        ).select_from(Trip),
        flt, codes=codes, user=user,
    ).join(TripFueling, TripFueling.trip_id == Trip.id)
    fuel_total, fuel_count = (await db.execute(fuel_stmt)).one()

    return {
        "count": int(count),
        "active": int(active),
        "total_km": int(total_km or 0),
        "fuelings_count": int(fuel_count or 0),
        "fuelings_amount": float(fuel_total or 0),
    }


# --- nabídky do filtru ------------------------------------------------

async def filter_vehicles(db: AsyncSession, *, codes: set[str], user: User) -> list[Vehicle]:
    """Jen vozidla, která uživatel vidí - jinak by výběr prozradil, že
    skryté vozidlo existuje."""
    stmt = select(Vehicle).order_by(Vehicle.license_plate)
    visible = visible_vehicles_condition(codes, user)
    if visible is not None:
        stmt = stmt.where(visible)
    return list((await db.execute(stmt)).scalars().all())


async def filter_drivers(db: AsyncSession, *, codes: set[str], user: User) -> list[User]:
    """Lidé, kteří opravdu nějakou viditelnou jízdu mají.

    Ne celý seznam uživatelů: nabízet ve filtru padesát jmen, z nichž
    čtyřicet nikdy nejelo, je jen delší rolování."""
    primary = _base_query(select(Trip.primary_driver_id), LogbookFilter(), codes=codes, user=user)
    extra = _base_query(
        select(TripDriver.user_id).select_from(Trip), LogbookFilter(), codes=codes, user=user,
    ).join(TripDriver, TripDriver.trip_id == Trip.id)

    stmt = (
        select(User)
        .where(User.id.in_(primary.union(extra).subquery().select()))
        .order_by(User.full_name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_trip_ids(db: AsyncSession, flt: LogbookFilter, *, codes: set[str], user: User) -> list[uuid.UUID]:
    """Jen ID - používá se v testech a při kontrole rozsahu exportu."""
    stmt = _base_query(select(Trip.id), flt, codes=codes, user=user).order_by(Trip.started_at.desc())
    return list((await db.execute(stmt)).scalars().all())
