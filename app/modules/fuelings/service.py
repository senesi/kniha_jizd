"""Tankování a nabíjení (zadání 15, Etapa 5).

Jeden tok pro obojí - liší se jen jednotka (l / kWh) a slovo. Rozhoduje
o tom `app/core/fuel.py`, tenhle modul se na `fuel_type` neptá sám.

Povinné je jen datum a množství. Všechno ostatní (cena, cena za
jednotku, stanice, stav km, druh paliva, fotografie účtenky, poznámka)
je nepovinné - zadání 15 to tak chce a řidič u pumpy nemá čas vyplňovat
formulář.

**Tankování existuje i bez jízdy.** U části vozidel se kniha jízd
nevede a eviduje se jen tankování a servis; elektromobil nabíjený přes
noc v depu navíc žádnou jízdu nemá vůbec. Rozdíl je jediný: **bez jízdy
je stav tachometru povinný**, protože z něj se počítá spotřeba
(app/core/consumption.py) a je to jediné, co u takového vozidla
tachometr posouvá.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.core import odometer
from app.core.audit import log_action
from app.core.fuel import UNITS, available_units, refuel_noun
from app.models.core import User
from app.models.fleet import FUEL_TYPES, Trip, TripFueling, Vehicle
from app.modules.fuelings import repository
from app.modules.vehicles import service as vehicles_service

MODULE = "fuelings"


class FuelingError(Exception):
    """Jednoznačně neplatný vstup - neuloží se."""


class FuelingWarning(Exception):
    """Podezřelé, ale možná správné. Stejný princip jako u jízd (zadání
    32): nese kód, který musí přijít zpátky jako potvrzení."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def add_fueling(
    db: AsyncSession, *, vehicle: Vehicle, trip: Trip | None = None, actor: User,
    fueled_at: date | None, quantity: float | None,
    unit: str, price_total_czk: float | None, price_per_unit_czk: float | None,
    station: str | None, odometer_km: int | None, fuel_type: str | None, note: str | None,
    confirmations: set[str], ocr_confirmed: bool = False,
    receipt: tuple[str, str | None, bytes] | None = None,
    receipt_attachment_id: uuid.UUID | None = None,
) -> TripFueling:
    """`trip=None` je tankování mimo jízdu. Vozidlo se předává vždycky -
    i u jízdy je to `trip.vehicle`, jen se na něj nečeká."""
    noun = refuel_noun(vehicle.fuel_type)

    if fueled_at is None:
        raise FuelingError("Datum je povinné.")
    if fueled_at > date.today():
        raise FuelingError("Datum nemůže být v budoucnosti.")
    if quantity is None or quantity <= 0:
        raise FuelingError(f"Množství musí být větší než nula ({noun.lower()} bez množství nemá smysl).")

    if unit not in UNITS:
        raise FuelingError("Neplatná jednotka.")
    # Jednotku nabízíme podle pohonu vozidla; cokoliv jiného je chyba
    # formuláře, ne volba uživatele.
    allowed = available_units(vehicle.fuel_type)
    if unit not in allowed:
        raise FuelingError(
            f"U tohoto vozidla se eviduje {'/'.join(allowed)}, ne {unit}."
        )

    if fuel_type is not None and fuel_type not in FUEL_TYPES:
        raise FuelingError("Neplatný druh paliva.")
    for label, value in (("Cena", price_total_czk), ("Cena za jednotku", price_per_unit_czk)):
        if value is not None and value < 0:
            raise FuelingError(f"{label} nemůže být záporná.")

    await _check_quantity(db, unit=unit, quantity=quantity, confirmations=confirmations)
    _check_against_capacity(vehicle, unit=unit, quantity=quantity, confirmations=confirmations)
    _check_odometer(vehicle, trip, odometer_km, confirmations)

    fueling = TripFueling(
        trip_id=trip.id if trip is not None else None,
        # Vždycky vyplněné, i bez jízdy: reporting na vozidlo i výpočet
        # spotřeby jdou odsud, ne přes trips.
        vehicle_id=vehicle.id,
        fueled_at=fueled_at,
        quantity=quantity,
        unit=unit,
        price_total_czk=price_total_czk if price_total_czk else None,
        price_per_unit_czk=_derive_per_unit(quantity, price_total_czk, price_per_unit_czk),
        station=(station or "").strip() or None,
        odometer_km=odometer_km,
        fuel_type=fuel_type,
        note=(note or "").strip() or None,
        ocr_confirmed=ocr_confirmed,
        created_by=actor.id,
    )
    db.add(fueling)
    await db.flush()

    # Tankování posouvá stav vozidla stejným pravidlem jako jízda a
    # servis - nikdy dolů (app/core/odometer.py). U vozidel bez knihy
    # jízd je to jediné, co tachometr posune.
    odometer_moved = odometer.advance(vehicle, odometer_km)

    await _attach_receipt(
        db, vehicle_id=vehicle.id, trip_id=trip.id if trip is not None else None,
        fueling_id=fueling.id, actor_id=actor.id,
        receipt=receipt, attachment_id=receipt_attachment_id,
    )

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="fueling",
        entity_id=str(fueling.id),
        after_data={
            "trip_id": str(trip.id) if trip is not None else None,
            "vehicle_id": str(vehicle.id),
            "quantity": float(quantity), "unit": unit,
            "odometer_km": odometer_km,
            "odometer_moved_to": vehicle.current_odometer_km if odometer_moved else None,
            "price_total_czk": float(price_total_czk) if price_total_czk else None,
            "ocr_confirmed": ocr_confirmed,
            "confirmations": sorted(confirmations),
        },
    )
    await db.commit()
    return await repository.get(db, fueling.id)


async def delete_fueling(db: AsyncSession, *, fueling: TripFueling, actor: User) -> None:
    """Smazání překlepu. Účtenka (příloha) zůstává - je to doklad, který
    se soft-deletem řeší zvlášť."""
    await log_action(
        db, user_id=actor.id, action="delete", module=MODULE, entity_type="fueling",
        entity_id=str(fueling.id),
        before_data={"quantity": float(fueling.quantity), "unit": fueling.unit},
    )
    await db.delete(fueling)
    await db.commit()


def _derive_per_unit(quantity, price_total, price_per_unit) -> float | None:
    """Cenu za jednotku dopočítá, jen když ji uživatel nezadal. Nikdy
    nepřepisuje to, co člověk napsal - na účtence může být zaokrouhleno
    jinak, než by vyšlo z dělení."""
    if price_per_unit:
        return price_per_unit
    if price_total and quantity:
        return round(float(price_total) / float(quantity), 2)
    return None


async def _check_quantity(db: AsyncSession, *, unit: str, quantity: float, confirmations: set[str]) -> None:
    """Tvrdý strop z nastavení - nad ním je to zjevně chyba (zadání 25:
    „množství paliva musí být v rozumném rozsahu")."""
    key = "charging_max_kwh" if unit == "kWh" else "fueling_max_liters"
    limit = await app_settings.get_value(db, key)
    if quantity > limit:
        raise FuelingError(
            f"Množství {quantity:g} {unit} přesahuje povolené maximum {limit} {unit}. "
            "Zkontrolujte zadanou hodnotu."
        )


def _check_against_capacity(vehicle, *, unit: str, quantity: float, confirmations: set[str]) -> None:
    """Víc, než se do vozidla vejde, je podezřelé - ale legitimní: řidič
    mohl natankovat i kanystr. Proto jen varování."""
    from app.core.fuel import capacity_for

    capacity, capacity_unit = capacity_for(vehicle)
    if not capacity or capacity_unit != unit:
        return
    if quantity > float(capacity) * 1.1 and "over_capacity" not in confirmations:
        raise FuelingWarning(
            "over_capacity",
            f"Zadané množství {quantity:g} {unit} je víc, než je kapacita vozidla "
            f"({float(capacity):g} {unit}). Je to správně?",
        )


def _check_odometer(
    vehicle: Vehicle, trip: Trip | None, odometer_km: int | None, confirmations: set[str],
) -> None:
    """U jízdy je stav km nepovinný, mimo jízdu povinný.

    Mimo jízdu je totiž jediné, z čeho se dá spočítat spotřeba a co
    posune tachometr vozidla - záznam bez něj by byl k ničemu právě u
    vozidel, kvůli kterým tahle cesta vznikla."""
    if odometer_km is None:
        if trip is None:
            raise FuelingError(
                "U tankování mimo jízdu je stav tachometru povinný – "
                "počítá se z něj spotřeba."
            )
        return

    if odometer_km < 0:
        raise FuelingError("Stav tachometru nemůže být záporný.")

    if trip is not None:
        if odometer_km < trip.start_odometer_km and "odometer_before_trip" not in confirmations:
            raise FuelingWarning(
                "odometer_before_trip",
                f"Zadaný stav {odometer_km:,} km je nižší než na začátku jízdy "
                f"({trip.start_odometer_km:,} km).".replace(",", " "),
            )
        return

    # Mimo jízdu se srovnává s posledním známým stavem vozidla. Nižší
    # hodnota nemusí být chyba (opožděně dopsaná stará účtenka), ale
    # chce potvrdit - stejný princip jako u přezutí.
    if odometer_km < vehicle.current_odometer_km and "odometer_lower" not in confirmations:
        raise FuelingWarning(
            "odometer_lower",
            f"Zadaný stav {odometer_km:,} km je nižší než poslední známý "
            f"({vehicle.current_odometer_km:,} km). Je to správně?".replace(",", " "),
        )


async def _attach_receipt(
    db: AsyncSession, *, vehicle_id: uuid.UUID, trip_id: uuid.UUID | None, fueling_id: uuid.UUID,
    actor_id: uuid.UUID, receipt: tuple[str, str | None, bytes] | None, attachment_id: uuid.UUID | None,
) -> None:
    """Účtenka se připojí buď rovnou z formuláře, nebo - po potvrzování
    OCR - napojením té, která se uložila už při prvním odeslání. Stejný
    princip jako u fotky tachometru (docs/ROZHODNUTI.md R11)."""
    if attachment_id is not None:
        try:
            await vehicles_service.link_attachment_to_fueling(
                db, attachment_id=attachment_id, vehicle_id=vehicle_id,
                trip_id=trip_id, fueling_id=fueling_id,
            )
        except ValueError as exc:
            raise FuelingError(str(exc)) from exc
        return
    if receipt is None:
        return
    filename, content_type, data = receipt
    await vehicles_service.add_attachment(
        db, vehicle_id=vehicle_id, kind="fuel_receipt", original_filename=filename,
        content_type=content_type, data=data, actor_id=actor_id, trip_id=trip_id,
        fueling_id=fueling_id, commit=False,
    )


def can_manage_fueling(trip: Trip, actor: User, codes: set[str], vehicle) -> bool:
    """Kdo smí sahat na tankování jízdy - stejný okruh jako u jejího
    uzavření. Kdo jízdu jel, ten k ní doplňuje účtenky."""
    from app.modules.trips.service import can_end_trip

    return can_end_trip(trip, actor, codes, vehicle)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
