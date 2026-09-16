"""Životní cyklus výpůjčky (zadání 8/11/12/27/32).

`active` -> `completed`, výjimečně `cancelled` administrátorem. Řádek
jízdy se nikdy nemaže ani nerecykluje.

Validace drží pravidlo ze zadání 32: co je **jednoznačně** neplatné
(konečný stav menší než počáteční, stav km jdoucí zpět) se odmítne; co
je jen **podezřelé** (velký skok tachometru, vozidlo v servisu) se
ukáže jako varování a pustí dál po potvrzení. Každé takové potvrzení
skončí v auditu, takže je z historie vidět, že to člověk viděl a
rozhodl.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.core.audit import log_action
from app.core.fuel import level_label
from app.models.core import User
from app.models.fleet import TRIP_PURPOSES, Trip, TripDriver, TripNote, Vehicle
from app.modules.notifications import service as notifications
from app.modules.reservations import repository as reservations_repository
from app.modules.reservations import service as reservations_service
from app.modules.trips import repository
from app.modules.vehicles import service as vehicles_service

MODULE = "trips"


class TripError(Exception):
    """Jednoznačně neplatný vstup - jízda se neuloží."""


class TripWarning(Exception):
    """Podezřelá, ale možná správná hodnota. Nese text pro uživatele a
    kód potvrzení, které musí přijít zpátky ve formuláři, aby se akce
    provedla (`confirm=<code>`)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- zahájení ---------------------------------------------------------

async def start_trip(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, start_odometer_km: int,
    start_fuel_level: int | None, confirmations: set[str],
    odometer_photo: tuple[str, str | None, bytes] | None = None,
    odometer_attachment_id: uuid.UUID | None = None,
) -> Trip:
    if not vehicle.is_active:
        raise TripError("Vozidlo je neaktivní a nelze si ho půjčit.")

    existing = await repository.get_active_trip_for_vehicle(db, vehicle.id)
    if existing is not None:
        raise TripError(
            f"Vozidlo má už otevřenou výpůjčku ({existing.primary_driver.full_name}, "
            f"od {existing.started_at.strftime('%d.%m.%Y %H:%M')}). Nejdřív ji ukončete."
        )

    # Vozidlo v servisu / mimo provoz se nezakazuje natvrdo - může jít o
    # nutnou jízdu do servisu. Ale řidič to musí vidět a potvrdit.
    if vehicle.status != "available" and "vehicle_status" not in confirmations:
        label = "v servisu" if vehicle.status == "in_service" else "mimo provoz"
        raise TripWarning("vehicle_status", f"Vozidlo je označené jako {label}. Opravdu chcete zahájit výpůjčku?")

    _check_start_odometer(vehicle, start_odometer_km)
    _check_fuel_level(vehicle, start_fuel_level)

    # Zadání 10: cizí rezervace výpůjčku NEBLOKUJE, ale musí se zobrazit a
    # potvrdit. Vlastní rezervace se naopak tiše naplní.
    now = datetime.now(timezone.utc)
    covering = await reservations_repository.find_covering(db, vehicle_id=vehicle.id, moment=now)
    own_reservation = None
    conflicting = None
    if covering is not None:
        if covering.kind == "reservation" and covering.user_id == actor.id:
            own_reservation = covering
        elif "reservation" not in confirmations:
            raise TripWarning(
                "reservation",
                f"Vozidlo je v tomto termínu rezervováno: {reservations_service.describe(covering)}.",
            )
        else:
            conflicting = covering

    trip = Trip(
        vehicle_id=vehicle.id,
        primary_driver_id=actor.id,
        started_by=actor.id,
        started_at=datetime.now(timezone.utc),
        start_odometer_km=start_odometer_km,
        start_fuel_level=start_fuel_level,
        status="active",
        reservation_id=own_reservation.id if own_reservation else None,
        # Potvrzení přejezdu cizí rezervace zůstává v historii jízdy
        # (zadání 10: „Potvrzení uložit do historie").
        reservation_conflict_id=conflicting.id if conflicting else None,
        reservation_override_at=now if conflicting else None,
    )
    db.add(trip)
    await db.flush()

    if own_reservation is not None:
        await reservations_service.mark_fulfilled(db, own_reservation)

    # Fotka tachometru je důkazní podklad k té jízdě - zapisuje se ve
    # stejné transakci, aby nemohla vzniknout jízda bez ní ani fotka bez
    # jízdy (commit=False).
    await _attach_odometer_photo(
        db, vehicle_id=vehicle.id, trip_id=trip.id, kind="odometer_start", actor_id=actor.id,
        photo=odometer_photo, attachment_id=odometer_attachment_id,
    )

    # Stav vozidla se posouvá už při startu: co řidič viděl na tachometru,
    # je novější údaj než poslední uzavřená jízda.
    _apply_vehicle_state(vehicle, odometer_km=start_odometer_km, fuel_level=start_fuel_level)

    await log_action(
        db, user_id=actor.id, action="trip_start", module=MODULE, entity_type="trip", entity_id=str(trip.id),
        after_data={
            "vehicle_id": str(vehicle.id), "start_odometer_km": start_odometer_km,
            "start_fuel_level": start_fuel_level, "confirmations": sorted(confirmations),
            "reservation_id": str(own_reservation.id) if own_reservation else None,
            "reservation_conflict_id": str(conflicting.id) if conflicting else None,
        },
    )
    await db.commit()

    await notifications.notify_trip_started(db, vehicle=vehicle, trip=trip, actor=actor)
    return await repository.get_trip(db, trip.id)


# --- ukončení ---------------------------------------------------------

async def end_trip(
    db: AsyncSession, *, trip: Trip, actor: User, end_odometer_km: int, end_fuel_level: int | None,
    purpose_code: str | None, purpose_text: str | None, route_text: str, note: str | None,
    confirmations: set[str], odometer_photo: tuple[str, str | None, bytes] | None = None,
    odometer_attachment_id: uuid.UUID | None = None,
) -> Trip:
    if trip.status != "active":
        raise TripError("Tato jízda už je uzavřená.")

    if end_odometer_km < trip.start_odometer_km:
        raise TripError(
            f"Konečný stav tachometru ({end_odometer_km:,} km) nemůže být nižší než počáteční "
            f"({trip.start_odometer_km:,} km).".replace(",", " ")
        )

    if not route_text.strip():
        raise TripError("Trasa je povinná.")
    if purpose_code not in TRIP_PURPOSES:
        raise TripError("Vyberte účel jízdy.")
    if purpose_code == "jine" and not (purpose_text or "").strip():
        raise TripError("U účelu „jiný“ doplňte, o co šlo.")

    _check_fuel_level(trip.vehicle, end_fuel_level)

    distance = end_odometer_km - trip.start_odometer_km
    jump_limit = await app_settings.get_value(db, "odometer_jump_warn_km")
    if distance > jump_limit and "odometer_jump" not in confirmations:
        raise TripWarning(
            "odometer_jump",
            f"Ujetá vzdálenost {distance:,} km je nezvykle vysoká (práh je {jump_limit:,} km). "
            "Zkontrolujte stav tachometru.".replace(",", " "),
        )

    trip.end_odometer_km = end_odometer_km
    trip.end_fuel_level = end_fuel_level
    trip.purpose_code = purpose_code
    trip.purpose_text = (purpose_text or "").strip() or None
    trip.route_text = route_text.strip()
    trip.note = (note or "").strip() or None
    trip.ended_at = datetime.now(timezone.utc)
    trip.ended_by = actor.id
    trip.status = "completed"
    await db.flush()

    await _attach_odometer_photo(
        db, vehicle_id=trip.vehicle_id, trip_id=trip.id, kind="odometer_end", actor_id=actor.id,
        photo=odometer_photo, attachment_id=odometer_attachment_id,
    )

    _apply_vehicle_state(trip.vehicle, odometer_km=end_odometer_km, fuel_level=end_fuel_level)

    await log_action(
        db, user_id=actor.id, action="trip_end", module=MODULE, entity_type="trip", entity_id=str(trip.id),
        after_data={
            "end_odometer_km": end_odometer_km, "distance_km": distance, "end_fuel_level": end_fuel_level,
            "purpose_code": purpose_code, "route_text": trip.route_text,
            "confirmations": sorted(confirmations),
        },
    )
    await db.commit()

    await notifications.notify_trip_ended(db, vehicle=trip.vehicle, trip=trip, actor=actor)
    return await repository.get_trip(db, trip.id)


async def _attach_odometer_photo(
    db: AsyncSession, *, vehicle_id: uuid.UUID, trip_id: uuid.UUID, kind: str, actor_id: uuid.UUID,
    photo: tuple[str, str | None, bytes] | None, attachment_id: uuid.UUID | None,
) -> None:
    """Fotka tachometru se k jízdě připojí buď rovnou z formuláře, nebo -
    když proběhlo potvrzování OCR - napojením té, která se uložila už při
    prvním odeslání. Obojí ve stejné transakci jako jízda, aby nemohla
    vzniknout jízda bez své fotky ani naopak."""
    if attachment_id is not None:
        try:
            await vehicles_service.link_attachment_to_trip(
                db, attachment_id=attachment_id, vehicle_id=vehicle_id, trip_id=trip_id,
            )
        except ValueError as exc:
            # Podvržené nebo zastaralé id ve skrytém poli - srozumitelná
            # chyba formuláře, ne pád na 500.
            raise TripError(str(exc)) from exc
        return
    if photo is None:
        return
    filename, content_type, data = photo
    await vehicles_service.add_attachment(
        db, vehicle_id=vehicle_id, kind=kind, original_filename=filename, content_type=content_type,
        data=data, actor_id=actor_id, trip_id=trip_id, commit=False,
    )


# --- další řidiči (zadání 12) -----------------------------------------

async def add_driver(db: AsyncSession, *, trip: Trip, user_id: uuid.UUID, actor: User) -> None:
    if user_id == trip.primary_driver_id:
        raise TripError("Tento uživatel je už primárním řidičem jízdy.")
    if any(assignment.user_id == user_id for assignment in trip.extra_drivers):
        raise TripError("Tento řidič už je u jízdy uvedený.")

    db.add(TripDriver(trip_id=trip.id, user_id=user_id, added_by=actor.id))
    await log_action(
        db, user_id=actor.id, action="driver_add", module=MODULE, entity_type="trip", entity_id=str(trip.id),
        after_data={"user_id": str(user_id)},
    )
    await db.commit()


async def remove_driver(db: AsyncSession, *, trip: Trip, user_id: uuid.UUID, actor: User) -> None:
    assignment = next((row for row in trip.extra_drivers if row.user_id == user_id), None)
    if assignment is None:
        raise TripError("Tento řidič u jízdy není.")
    await db.delete(assignment)
    await log_action(
        db, user_id=actor.id, action="driver_remove", module=MODULE, entity_type="trip", entity_id=str(trip.id),
        before_data={"user_id": str(user_id)},
    )
    await db.commit()


# --- poznámky a administrativní zásah ---------------------------------

async def add_note(db: AsyncSession, *, trip: Trip, text: str, actor: User) -> None:
    """Přípisky k jízdě jsou samostatné řádky, ne přepsání toho, co
    napsal řidič při uzavírání (zadání 27 - historické údaje se
    neupravují, opravy se evidují)."""
    if not text.strip():
        raise TripError("Poznámka nesmí být prázdná.")
    db.add(TripNote(trip_id=trip.id, author_id=actor.id, text=text.strip()))
    await log_action(
        db, user_id=actor.id, action="note_add", module=MODULE, entity_type="trip", entity_id=str(trip.id),
    )
    await db.commit()


async def cancel_trip(db: AsyncSession, *, trip: Trip, reason: str, actor: User) -> Trip:
    """Zrušení omylem zahájené jízdy. Nikdy nemaže řádek - jen ho uzavře
    jako `cancelled`, takže z historie zůstane vidět, že vznikla.

    Stav vozidla se nevrací zpátky: tachometr zaznamenaný při startu je
    skutečný údaj, který někdo na vozidle viděl."""
    if trip.status != "active":
        raise TripError("Zrušit lze jen probíhající jízdu.")
    if not reason.strip():
        raise TripError("Důvod zrušení je povinný.")

    trip.status = "cancelled"
    trip.ended_at = datetime.now(timezone.utc)
    trip.ended_by = actor.id
    db.add(TripNote(trip_id=trip.id, author_id=actor.id, text=f"Jízda zrušena: {reason.strip()}"))
    await log_action(
        db, user_id=actor.id, action="trip_cancel", module=MODULE, entity_type="trip", entity_id=str(trip.id),
        after_data={"reason": reason.strip()},
    )
    await db.commit()
    return await repository.get_trip(db, trip.id)


# --- validace ---------------------------------------------------------

def _check_start_odometer(vehicle: Vehicle, start_odometer_km: int) -> None:
    if start_odometer_km < 0:
        raise TripError("Stav tachometru nemůže být záporný.")
    if start_odometer_km < vehicle.current_odometer_km:
        # Tvrdá chyba, ne varování: tohle je přesně to, co zadání 5
        # zakazuje udělat běžnou jízdou. Cesta ven existuje, ale vede
        # přes administrativní opravu se zdůvodněním.
        raise TripError(
            f"Zadaný stav tachometru ({start_odometer_km:,} km) je nižší než poslední známý "
            f"({vehicle.current_odometer_km:,} km). Pokud je hodnota na vozidle opravdu nižší, "
            "musí ji nejdřív administrativně opravit správce vozidla.".replace(",", " ")
        )


def _check_fuel_level(vehicle: Vehicle, fuel_level: int | None) -> None:
    if fuel_level is None:
        return
    if not 0 <= fuel_level <= 100:
        raise TripError(f"{level_label(vehicle.fuel_type)} musí být mezi 0 a 100 %.")


def _apply_vehicle_state(vehicle: Vehicle, *, odometer_km: int, fuel_level: int | None) -> None:
    vehicle.current_odometer_km = max(vehicle.current_odometer_km, odometer_km)
    if fuel_level is not None:
        vehicle.current_fuel_level = fuel_level
    vehicle.state_updated_at = datetime.now(timezone.utc)


# --- oprávnění k jízdě -------------------------------------------------

def can_end_trip(trip: Trip, actor: User, codes: set[str], vehicle: Vehicle) -> bool:
    """Zavřít jízdu smí ten, kdo ji jel, kdokoliv vedený jako další řidič,
    správce vozidla (zapomenutá výpůjčka se musí dát uzavřít) a
    administrátor jízd."""
    if trip.primary_driver_id == actor.id:
        return True
    if any(assignment.user_id == actor.id for assignment in trip.extra_drivers):
        return True
    if "fleet.trip.manage" in codes or "fleet.vehicle.manage" in codes:
        return True
    return "fleet.vehicle.manage.own" in codes and vehicle.responsible_user_id == actor.id
