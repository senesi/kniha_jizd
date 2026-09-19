"""Tankování a nabíjení (Etapa 5, zadání 15).

Formulář je krátký schválně: datum a množství jsou povinné, zbytek se
schová pod rozbalovátko. Řidič u pumpy má v ruce telefon a účtenku, ne
čas na dvacet polí.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash, ocr
from app.core.access import assert_vehicle_visible
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_permission
from app.core.fuel import available_units, default_unit
from app.core.photos import PhotoTooLarge, UnsupportedPhotoType
from app.core.templates import render_page
from app.models.core import User
from app.core.consumption import for_vehicle as consumption_for_vehicle
from app.models.fleet import FUEL_TYPES, Trip, Vehicle
from app.modules.fuelings import repository, service
from app.modules.trips import repository as trips_repository
from app.modules.vehicles import repository as vehicles_repository
from app.modules.vehicles import service as vehicles_service

fuelings_router = APIRouter(tags=["fuelings-web"])
trip_fuelings_router = APIRouter(tags=["fuelings-web"])
vehicle_fuelings_router = APIRouter(tags=["fuelings-web"])

CREATE = "fleet.trip.create"


# --- pomocné ----------------------------------------------------------

async def _load_trip(db: AsyncSession, trip_id: uuid.UUID, *, user: User) -> Trip:
    trip = await trips_repository.get_trip(db, trip_id)
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Jízda nebyla nalezena.")
    assert_vehicle_visible(await get_user_permission_codes(db, user.id), trip.vehicle, user)
    return trip


async def _assert_can_manage(db: AsyncSession, trip: Trip, user: User) -> None:
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_manage_fueling(trip, user, codes, trip.vehicle):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tankování k této jízdě může zadat jen její řidič nebo správce vozidla.",
        )


def _to_float(raw) -> float | None:
    text = str(raw or "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def _to_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(form, name: str) -> str | None:
    raw = form.get(name)
    return raw if isinstance(raw, str) else None


async def _read_receipt(upload: UploadFile | None):
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if not data:
        return None
    return upload.filename, upload.content_type, data


async def _read_receipt_now(
    db: AsyncSession, *, photo, vehicle_id, trip_id, actor_id,
):
    """Přečte účtenku a **uloží ji**, ať už se něco přečetlo, nebo ne.

    Vrací `(suggestion, attachment_id, error)`. Účtenka se ukládá i při
    neúspěchu schválně: uživatel ji už jednou vyfotil a nutit ho to
    opakovat jen proto, že OCR nic nenašlo, by bylo horší než to
    nepřečíst."""
    # Slovník už zadaných stanic - viz ocr._match_known.
    reading = await ocr.read_receipt(photo[2], await repository.known_stations(db))
    try:
        attachment = await vehicles_service.add_attachment(
            db, vehicle_id=vehicle_id, kind="fuel_receipt",
            original_filename=photo[0], content_type=photo[1], data=photo[2],
            actor_id=actor_id, trip_id=trip_id,
        )
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return None, None, f"Účtenku se nepodařilo uložit: {error}"
    return reading, attachment.id, None


def _form_context(vehicle: Vehicle, trip: Trip | None = None, **extra) -> dict:
    """`trip=None` je tankování mimo jízdu - formulář je jinak stejný,
    jen má povinný stav tachometru a vrací se na kartu vozidla."""
    return {
        "trip": trip,
        "vehicle": vehicle,
        "units": available_units(vehicle.fuel_type),
        "default_unit": default_unit(vehicle.fuel_type),
        "fuel_types": FUEL_TYPES,
        # Bez zapnutého OCR nemá smysl nabízet tlačítko, které nic
        # nepřečte.
        "ocr_available": ocr.is_configured(),
        **extra,
    }


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(await get_user_permission_codes(db, user.id), vehicle, user)
    return vehicle


# --- formulář ---------------------------------------------------------

@trip_fuelings_router.get("/trips/{trip_id}/fuelings/new")
async def fueling_new_form(
    request: Request,
    trip_id: uuid.UUID,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id, user=user)
    await _assert_can_manage(db, trip, user)
    return await render_page(
        request, "fueling_form.html", user, db,
        **_form_context(
            trip.vehicle, trip,
            error=None, warnings=[], ocr_suggestion=None, receipt_attachment_id=None,
            # Datum se předvyplní dneškem - řidič tankuje dnes, ne někdy.
            form={"fueled_at": date.today().isoformat()},
        ),
    )


@trip_fuelings_router.post("/trips/{trip_id}/fuelings/new", dependencies=[Depends(verify_csrf)])
async def fueling_create(
    request: Request,
    trip_id: uuid.UUID,
    receipt: UploadFile | None = File(None),
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id, user=user)
    await _assert_can_manage(db, trip, user)

    form = await request.form()
    payload = {
        "fueled_at": _text(form, "fueled_at"),
        "quantity": _text(form, "quantity"),
        "unit": _text(form, "unit") or default_unit(trip.vehicle.fuel_type),
        "price_total_czk": _text(form, "price_total_czk"),
        "price_per_unit_czk": _text(form, "price_per_unit_czk"),
        "station": _text(form, "station"),
        "odometer_km": _text(form, "odometer_km"),
        "fuel_type": _text(form, "fuel_type") or None,
        "note": _text(form, "note"),
    }
    confirmations = {value for value in form.getlist("confirm") if isinstance(value, str)}
    stashed_id = _stashed_id(form)

    async def rerender(error, warnings, suggestion=None, status_code=400, attachment_id=None):
        return await render_page(
            request, "fueling_form.html", user, db, status_code=status_code,
            **_form_context(
                trip.vehicle, trip, error=error, warnings=warnings, ocr_suggestion=suggestion,
                receipt_attachment_id=attachment_id or stashed_id, form=payload,
            ),
        )

    photo = await _read_receipt(receipt)
    # Tlačítko „Přečíst z účtenky" posílá formulář s formnovalidate, tedy
    # bez vyplněného množství - to má uživateli teprve nabídnout OCR.
    wants_reading = _text(form, "action") == "read_receipt"

    if wants_reading and photo is None:
        return await rerender("Nejdřív přiložte fotografii účtenky.", [])

    # OCR účtenky: když něco přečte a uživatel to ještě neviděl, ukáže se
    # mu to k potvrzení. Nikdy se neuloží samo (zadání 15/25).
    if photo is not None and "ocr" not in confirmations:
        reading, attachment_id, error = await _read_receipt_now(
            db, photo=photo, vehicle_id=trip.vehicle_id, trip_id=trip.id, actor_id=user.id,
        )
        if error:
            return await rerender(error, [])
        if reading is not None:
            return await rerender(None, [], suggestion=reading, status_code=200,
                                  attachment_id=attachment_id)
        if wants_reading:
            # Účtenka je uložená, jen z ní nic nevyšlo - ať to uživatel
            # ví a nečeká, že se pole doplní sama.
            return await rerender(
                "Z účtenky se nepodařilo nic přečíst. Uložila se, údaje prosím vyplňte ručně.",
                [], status_code=200, attachment_id=attachment_id,
            )
        # Fotka je uložená, takže se nesmí poslat znovu do add_fueling.
        photo = None
        stashed_id = attachment_id

    try:
        await service.add_fueling(
            db, vehicle=trip.vehicle, trip=trip, actor=user,
            fueled_at=_to_date(payload["fueled_at"]),
            quantity=_to_float(payload["quantity"]),
            unit=payload["unit"],
            price_total_czk=_to_float(payload["price_total_czk"]),
            price_per_unit_czk=_to_float(payload["price_per_unit_czk"]),
            station=payload["station"],
            odometer_km=_to_int(payload["odometer_km"]),
            fuel_type=payload["fuel_type"],
            note=payload["note"],
            confirmations=confirmations,
            ocr_confirmed="ocr" in confirmations,
            receipt=photo,
            receipt_attachment_id=stashed_id,
        )
    except service.FuelingWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.FuelingError as error:
        return await rerender(str(error), [])
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return await rerender(f"Účtenku se nepodařilo uložit: {error}", [])

    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "fueling_added")


def _stashed_id(form) -> uuid.UUID | None:
    raw = str(form.get("receipt_attachment_id") or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# --- smazání ----------------------------------------------------------

@fuelings_router.post("/fuelings/{fueling_id}/delete", dependencies=[Depends(verify_csrf)])
async def fueling_delete(
    fueling_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    fueling = await repository.get(db, fueling_id)
    if fueling is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Záznam nebyl nalezen.")
    trip = await _load_trip(db, fueling.trip_id, user=user)
    await _assert_can_manage(db, trip, user)
    await service.delete_fueling(db, fueling=fueling, actor=user)
    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "fueling_deleted")


# --- tankování mimo jízdu (z karty vozidla) ---------------------------
#
# Vozidla, u kterých se kniha jízd nevede, mají tankování a servis jako
# jediný zdroj dat; elektromobil nabíjený v depu žádnou jízdu nemá.
# Formulář i ukládání jsou tytéž jako u jízdy - liší se jen tím, že stav
# tachometru je povinný a návrat vede na vozidlo.

@vehicle_fuelings_router.get("/vehicles/{vehicle_id}/fuelings")
async def vehicle_fuelings(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id, user=user)
    fuelings = await repository.list_for_vehicle(db, vehicle.id, limit=200)
    return await render_page(
        request, "vehicle_fuelings.html", user, db,
        vehicle=vehicle,
        fuelings=fuelings,
        totals=await repository.totals_for_vehicle(db, vehicle.id),
        # Spotřeba se počítá ze VŠECH tankování vozidla, ne jen z těch
        # zobrazených - limit je vlastnost výpisu, ne dat.
        consumptions=consumption_for_vehicle(
            await repository.list_for_vehicle(db, vehicle.id, limit=10_000)
        ),
    )


@vehicle_fuelings_router.get("/vehicles/{vehicle_id}/fuelings/new")
async def vehicle_fueling_new_form(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id, user=user)
    return await render_page(
        request, "fueling_form.html", user, db,
        **_form_context(
            vehicle, None,
            error=None, warnings=[], ocr_suggestion=None, receipt_attachment_id=None,
            form={
                "fueled_at": date.today().isoformat(),
                # Předvyplní se poslední známý stav - u pumpy se obvykle
                # opíše číslo o něco vyšší, ne úplně nové.
                "odometer_km": str(vehicle.current_odometer_km),
            },
        ),
    )


@vehicle_fuelings_router.post("/vehicles/{vehicle_id}/fuelings/new", dependencies=[Depends(verify_csrf)])
async def vehicle_fueling_create(
    request: Request,
    vehicle_id: uuid.UUID,
    receipt: UploadFile | None = File(None),
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id, user=user)

    form = await request.form()
    payload = {
        "fueled_at": _text(form, "fueled_at"),
        "quantity": _text(form, "quantity"),
        "unit": _text(form, "unit") or default_unit(vehicle.fuel_type),
        "price_total_czk": _text(form, "price_total_czk"),
        "price_per_unit_czk": _text(form, "price_per_unit_czk"),
        "station": _text(form, "station"),
        "odometer_km": _text(form, "odometer_km"),
        "fuel_type": _text(form, "fuel_type") or None,
        "note": _text(form, "note"),
    }
    confirmations = {value for value in form.getlist("confirm") if isinstance(value, str)}
    stashed_id = _stashed_id(form)

    async def rerender(error, warnings, suggestion=None, status_code=400, attachment_id=None):
        return await render_page(
            request, "fueling_form.html", user, db, status_code=status_code,
            **_form_context(
                vehicle, None, error=error, warnings=warnings, ocr_suggestion=suggestion,
                receipt_attachment_id=attachment_id or stashed_id, form=payload,
            ),
        )

    photo = await _read_receipt(receipt)
    wants_reading = _text(form, "action") == "read_receipt"

    if wants_reading and photo is None:
        return await rerender("Nejdřív přiložte fotografii účtenky.", [])

    # Stejný postup jako u jízdy: OCR nikdy neuloží samo, jen nabídne
    # hodnoty k potvrzení (zadání 15/25).
    if photo is not None and "ocr" not in confirmations:
        reading, attachment_id, error = await _read_receipt_now(
            db, photo=photo, vehicle_id=vehicle.id, trip_id=None, actor_id=user.id,
        )
        if error:
            return await rerender(error, [])
        if reading is not None:
            return await rerender(None, [], suggestion=reading, status_code=200,
                                  attachment_id=attachment_id)
        if wants_reading:
            return await rerender(
                "Z účtenky se nepodařilo nic přečíst. Uložila se, údaje prosím vyplňte ručně.",
                [], status_code=200, attachment_id=attachment_id,
            )
        photo = None
        stashed_id = attachment_id

    try:
        await service.add_fueling(
            db, vehicle=vehicle, trip=None, actor=user,
            fueled_at=_to_date(payload["fueled_at"]),
            quantity=_to_float(payload["quantity"]),
            unit=payload["unit"],
            price_total_czk=_to_float(payload["price_total_czk"]),
            price_per_unit_czk=_to_float(payload["price_per_unit_czk"]),
            station=payload["station"],
            odometer_km=_to_int(payload["odometer_km"]),
            fuel_type=payload["fuel_type"],
            note=payload["note"],
            confirmations=confirmations,
            ocr_confirmed="ocr" in confirmations,
            receipt=photo,
            receipt_attachment_id=stashed_id,
        )
    except service.FuelingWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.FuelingError as error:
        return await rerender(str(error), [])
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return await rerender(f"Účtenku se nepodařilo uložit: {error}", [])

    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/fuelings", "fueling_added")
