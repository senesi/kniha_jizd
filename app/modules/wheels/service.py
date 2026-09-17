"""Sady kol a přezutí.

Jestli je sada nasazená, se **neukládá** - odvozuje se z otevřeného
`WheelFitment` (`removed_at IS NULL`). Uložený příznak by se dřív nebo
později rozešel se skutečností; stejná úvaha jako u „vypůjčeného"
vozidla (docs/ROZHODNUTI.md R4).

Přezutí je jedna transakce: sundat starou sadu a nasadit novou musí
proběhnout buď obojí, nebo nic. Kdyby se to rozpadlo uprostřed, zůstalo
by vozidlo buď bez kol, nebo se dvěma sadami - a to druhé databáze
stejně nedovolí (částečný unikátní index).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.models.core import User
from app.models.fleet import WHEEL_SEASONS, Vehicle, WheelFitment, WheelSet
from app.modules.vehicles import service as vehicles_service
from app.modules.wheels import repository

MODULE = "wheels"

ACTIVE_CONSTRAINT = "uq_fleet_wheel_fitments_one_active_per_vehicle"

# Pod tuhle hloubku už pneumatika nesmí na silnici (letní 1,6 mm, zimní
# 4 mm) - hlásí se to jako varování, ne zákaz.
LEGAL_TREAD_MM = {"summer": 1.6, "winter": 4.0}


class WheelError(Exception):
    """Jednoznačně neplatný vstup."""


class WheelWarning(Exception):
    """Podezřelé, ale možná správné - projde po potvrzení."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- sady ---------------------------------------------------------------

async def create_set(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, season: str, brand: str | None,
    model: str | None, size: str | None, purchased_at: date | None,
    purchase_odometer_km: int | None, dot_code: str | None, tread_depth_mm: float | None,
    note: str | None, photo: tuple[str, str | None, bytes] | None = None,
) -> WheelSet:
    if season not in WHEEL_SEASONS:
        raise WheelError("Vyberte, jestli jde o letní, nebo zimní sadu.")
    if purchased_at and purchased_at > date.today():
        raise WheelError("Datum pořízení nemůže být v budoucnosti.")
    if tread_depth_mm is not None and not 0 < tread_depth_mm <= 20:
        raise WheelError("Hloubka dezénu musí být mezi 0 a 20 mm.")
    if purchase_odometer_km is not None and purchase_odometer_km < 0:
        raise WheelError("Stav tachometru nemůže být záporný.")

    wheel_set = WheelSet(
        vehicle_id=vehicle.id,
        season=season,
        brand=(brand or "").strip() or None,
        model=(model or "").strip() or None,
        size=(size or "").strip() or None,
        purchased_at=purchased_at,
        purchase_odometer_km=purchase_odometer_km,
        dot_code=(dot_code or "").strip() or None,
        tread_depth_mm=tread_depth_mm,
        note=(note or "").strip() or None,
        created_by=actor.id,
    )
    db.add(wheel_set)
    await db.flush()

    if photo is not None:
        filename, content_type, data = photo
        await vehicles_service.add_attachment(
            db, vehicle_id=vehicle.id, kind="wheel_photo", original_filename=filename,
            content_type=content_type, data=data, actor_id=actor.id, wheel_set_id=wheel_set.id,
            commit=False,
        )

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="wheel_set",
        entity_id=str(wheel_set.id),
        after_data={
            "vehicle_id": str(vehicle.id), "season": season, "brand": wheel_set.brand,
            "size": wheel_set.size, "dot_code": wheel_set.dot_code,
            "tread_depth_mm": tread_depth_mm,
        },
    )
    await db.commit()
    return await repository.get_set(db, wheel_set.id)


async def update_set(
    db: AsyncSession, *, wheel_set: WheelSet, actor: User, brand: str | None, model: str | None,
    size: str | None, dot_code: str | None, tread_depth_mm: float | None, note: str | None,
) -> WheelSet:
    """Sezóna se nemění - sada je fyzická věc a přeznačení zimní sady na
    letní by rozbilo historii přezutí. Když je špatně, založí se nová."""
    if tread_depth_mm is not None and not 0 < tread_depth_mm <= 20:
        raise WheelError("Hloubka dezénu musí být mezi 0 a 20 mm.")

    before = {
        "brand": wheel_set.brand, "model": wheel_set.model, "size": wheel_set.size,
        "dot_code": wheel_set.dot_code, "tread_depth_mm": wheel_set.tread_depth_mm,
    }
    wheel_set.brand = (brand or "").strip() or None
    wheel_set.model = (model or "").strip() or None
    wheel_set.size = (size or "").strip() or None
    wheel_set.dot_code = (dot_code or "").strip() or None
    wheel_set.tread_depth_mm = tread_depth_mm
    wheel_set.note = (note or "").strip() or None
    await db.flush()

    await log_action(
        db, user_id=actor.id, action="update", module=MODULE, entity_type="wheel_set",
        entity_id=str(wheel_set.id), before_data=before,
        after_data={
            "brand": wheel_set.brand, "model": wheel_set.model, "size": wheel_set.size,
            "dot_code": wheel_set.dot_code, "tread_depth_mm": tread_depth_mm,
        },
    )
    await db.commit()
    return await repository.get_set(db, wheel_set.id)


async def delete_set(db: AsyncSession, *, wheel_set: WheelSet, actor: User) -> None:
    """Vyřazení sady. Soft delete - historie přezutí na ni odkazuje."""
    active = await repository.get_active_fitment_for_set(db, wheel_set.id)
    if active is not None:
        raise WheelError("Sada je právě nasazená – nejdřív ji sundejte.")

    wheel_set.deleted_at = datetime.now(timezone.utc)
    await log_action(
        db, user_id=actor.id, action="delete", module=MODULE, entity_type="wheel_set",
        entity_id=str(wheel_set.id), before_data={"season": wheel_set.season, "brand": wheel_set.brand},
    )
    await db.commit()


# --- přezutí ------------------------------------------------------------

async def change_wheels(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, wheel_set: WheelSet,
    fitted_at: date, odometer_km: int, note: str | None, confirmations: set[str],
) -> WheelFitment:
    """Přezutí jedním krokem: sundá se, co je na voze, a nasadí se nová
    sada. Obojí v jedné transakci."""
    if wheel_set.vehicle_id != vehicle.id:
        raise WheelError("Tato sada patří jinému vozidlu.")
    if wheel_set.deleted_at is not None:
        raise WheelError("Tato sada je vyřazená.")
    if fitted_at > date.today():
        raise WheelError("Datum přezutí nemůže být v budoucnosti.")
    if odometer_km < 0:
        raise WheelError("Stav tachometru nemůže být záporný.")

    current = await repository.get_active_fitment(db, vehicle.id)
    if current is not None and current.wheel_set_id == wheel_set.id:
        raise WheelError("Tato sada už je na vozidle nasazená.")

    # Stav km nižší než při nasazení předchozí sady by rozbil výpočet
    # nájezdu - a nižší než aktuální stav vozidla je podezřelý.
    if current is not None and odometer_km < current.fitted_odometer_km:
        raise WheelError(
            f"Stav {odometer_km:,} km je nižší než při nasazení předchozí sady "
            f"({current.fitted_odometer_km:,} km).".replace(",", " ")
        )
    if odometer_km < vehicle.current_odometer_km and "odometer_lower" not in confirmations:
        raise WheelWarning(
            "odometer_lower",
            f"Zadaný stav {odometer_km:,} km je nižší než poslední známý stav vozidla "
            f"({vehicle.current_odometer_km:,} km). Je to správně?".replace(",", " "),
        )

    _warn_on_tread(wheel_set, confirmations)

    removed_id = None
    if current is not None:
        current.removed_at = fitted_at
        current.removed_odometer_km = odometer_km
        removed_id = current.id
        await db.flush()

    fitment = WheelFitment(
        wheel_set_id=wheel_set.id,
        vehicle_id=vehicle.id,
        fitted_at=fitted_at,
        fitted_odometer_km=odometer_km,
        note=(note or "").strip() or None,
        created_by=actor.id,
    )
    db.add(fitment)
    try:
        # Savepoint, aby případné porušení unikátního indexu nezneplatnilo
        # celou session (viz docs/ROZHODNUTI.md R15).
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        if ACTIVE_CONSTRAINT in str(exc.orig):
            raise WheelError("Na vozidle je už jiná nasazená sada – zkuste to znovu.") from exc
        raise WheelError("Přezutí se nepodařilo uložit.") from exc

    await log_action(
        db, user_id=actor.id, action="fit", module=MODULE, entity_type="wheel_fitment",
        entity_id=str(fitment.id),
        before_data={"removed_fitment_id": str(removed_id)} if removed_id else None,
        after_data={
            "vehicle_id": str(vehicle.id), "wheel_set_id": str(wheel_set.id),
            "season": wheel_set.season, "fitted_at": fitted_at.isoformat(),
            "fitted_odometer_km": odometer_km,
            "confirmations": sorted(confirmations),
        },
    )
    await db.commit()
    return await repository.get_fitment(db, fitment.id)


async def remove_wheels(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, removed_at: date, odometer_km: int,
) -> None:
    """Sundání bez nasazení jiné sady - třeba když jde vozidlo do servisu."""
    current = await repository.get_active_fitment(db, vehicle.id)
    if current is None:
        raise WheelError("Na vozidle není evidovaná žádná nasazená sada.")
    if removed_at > date.today():
        raise WheelError("Datum nemůže být v budoucnosti.")
    if odometer_km < current.fitted_odometer_km:
        raise WheelError(
            f"Stav {odometer_km:,} km je nižší než při nasazení "
            f"({current.fitted_odometer_km:,} km).".replace(",", " ")
        )

    current.removed_at = removed_at
    current.removed_odometer_km = odometer_km
    await db.flush()

    await log_action(
        db, user_id=actor.id, action="remove", module=MODULE, entity_type="wheel_fitment",
        entity_id=str(current.id),
        after_data={
            "removed_at": removed_at.isoformat(), "removed_odometer_km": odometer_km,
            "distance_km": current.distance_km(vehicle.current_odometer_km),
        },
    )
    await db.commit()


def _warn_on_tread(wheel_set: WheelSet, confirmations: set[str]) -> None:
    """Dezén pod zákonným minimem je důvod k upozornění, ne k zákazu -
    aplikace nemá suplovat technickou kontrolu, ale nemá to ani mlčky
    přejít."""
    if wheel_set.tread_depth_mm is None or "low_tread" in confirmations:
        return
    limit = LEGAL_TREAD_MM.get(wheel_set.season)
    if limit and float(wheel_set.tread_depth_mm) < limit:
        raise WheelWarning(
            "low_tread",
            f"Sada má evidovaný dezén {float(wheel_set.tread_depth_mm):g} mm, což je pod zákonným "
            f"minimem {limit:g} mm pro {'zimní' if wheel_set.season == 'winter' else 'letní'} "
            "pneumatiky.",
        )


def set_distance_km(wheel_set: WheelSet, current_odometer_km: int) -> int:
    """Celkový nájezd na sadě přes všechna období, kdy byla nasazená."""
    return sum(fitment.distance_km(current_odometer_km) for fitment in wheel_set.fitments)
