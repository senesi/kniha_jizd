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
from app.models.fleet import FUEL_TYPES, Trip
from app.modules.fuelings import repository, service
from app.modules.trips import repository as trips_repository
from app.modules.vehicles import service as vehicles_service

fuelings_router = APIRouter(tags=["fuelings-web"])
trip_fuelings_router = APIRouter(tags=["fuelings-web"])

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


def _form_context(trip: Trip, **extra) -> dict:
    vehicle = trip.vehicle
    return {
        "trip": trip,
        "vehicle": vehicle,
        "units": available_units(vehicle.fuel_type),
        "default_unit": default_unit(vehicle.fuel_type),
        "fuel_types": FUEL_TYPES,
        **extra,
    }


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
            trip,
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
                trip, error=error, warnings=warnings, ocr_suggestion=suggestion,
                receipt_attachment_id=attachment_id or stashed_id, form=payload,
            ),
        )

    photo = await _read_receipt(receipt)

    # OCR účtenky: když něco přečte a uživatel to ještě neviděl, ukáže se
    # mu to k potvrzení. Nikdy se neuloží samo (zadání 15/25).
    if photo is not None and "ocr" not in confirmations:
        reading = await ocr.read_receipt(photo[2])
        if reading is not None:
            try:
                attachment = await vehicles_service.add_attachment(
                    db, vehicle_id=trip.vehicle_id, kind="fuel_receipt",
                    original_filename=photo[0], content_type=photo[1], data=photo[2],
                    actor_id=user.id, trip_id=trip.id,
                )
            except (PhotoTooLarge, UnsupportedPhotoType) as error:
                return await rerender(f"Účtenku se nepodařilo uložit: {error}", [])
            return await rerender(None, [], suggestion=reading, status_code=200, attachment_id=attachment.id)

    try:
        await service.add_fueling(
            db, trip=trip, actor=user,
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
