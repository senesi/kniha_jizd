"""Výpůjčky - zahájení, ukončení, další řidiči, poznámky (zadání 8/11/12).

Mobilní tok je záměrně jednokrokový: řidič otevře formulář, případně
vyfotí tachometr, potvrdí předvyplněná čísla a odešle. Druhá obrazovka
se objeví jen tehdy, když je opravdu co rozhodnout - OCR přečetlo jinou
hodnotu, než jakou člověk napsal, nebo je údaj podezřelý (zadání 32:
raději upozornit a vyžádat potvrzení než tvrdě blokovat).
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash, ocr
from app.core.access import MANAGE_ANY, assert_vehicle_visible
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_permission
from app.core.photos import PhotoTooLarge, UnsupportedPhotoType
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import TRIP_PURPOSES, Trip, Vehicle
from app.modules.reservations.calendar import LOCAL_TZ
from app.modules.trips import repository, service
from app.modules.vehicles import repository as vehicles_repository
from app.modules.vehicles import service as vehicles_service

vehicle_trips_router = APIRouter(tags=["trips-web"])
trips_router = APIRouter(tags=["trips-web"])

CREATE = "fleet.trip.create"
MANAGE = "fleet.trip.manage"


# --- pomocné ----------------------------------------------------------

async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User) -> Vehicle:
    """Zahájit jízdu lze jen s vozidlem, které uživatel vůbec smí vidět
    (požadavek D) - jinak by skryté vozidlo šlo vzít přímým odkazem."""
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(await get_user_permission_codes(db, user.id), vehicle, user)
    return vehicle


async def _load_trip(db: AsyncSession, trip_id: uuid.UUID) -> Trip:
    trip = await repository.get_trip(db, trip_id)
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Jízda nebyla nalezena.")
    return trip


def _to_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _confirmations(form) -> set[str]:
    """Kódy varování, která uživatel na potvrzovací obrazovce odklikl."""
    return {value for value in form.getlist("confirm") if isinstance(value, str)}


async def _read_photo(upload: UploadFile | None) -> tuple[str, str | None, bytes] | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if not data:
        return None
    return upload.filename, upload.content_type, data


async def _stash_photo_for_confirmation(
    db: AsyncSession, *, vehicle_id: uuid.UUID, kind: str, actor_id: uuid.UUID,
    photo: tuple[str, str | None, bytes],
) -> uuid.UUID:
    """Uloží fotku hned a vrátí její id do skrytého pole formuláře.

    Bez toho by se fotka při potvrzování OCR ztratila - prohlížeč souborový
    input předvyplnit neumí a nutit řidiče fotit tachometr podruhé je
    nepřijatelné. Fotka tím vznikne ještě před jízdou; když uživatel
    formulář opustí, zůstane u vozidla nenavázaná (do galerie vozidla se
    nedostane, ta bere jen kind="vehicle_photo").
    """
    filename, content_type, data = photo
    attachment = await vehicles_service.add_attachment(
        db, vehicle_id=vehicle_id, kind=kind, original_filename=filename, content_type=content_type,
        data=data, actor_id=actor_id,
    )
    return attachment.id


def _stashed_attachment_id(form) -> uuid.UUID | None:
    raw = str(form.get("odometer_attachment_id") or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _assert_can_end(db: AsyncSession, trip: Trip, actor: User) -> None:
    codes = await get_user_permission_codes(db, actor.id)
    if not service.can_end_trip(trip, actor, codes, trip.vehicle):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tuto výpůjčku může ukončit jen její řidič nebo správce vozidla.",
        )


# --- zahájení ---------------------------------------------------------

@vehicle_trips_router.get("/vehicles/{vehicle_id}/trips/start")
async def trip_start_form(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id, user=user)
    active = await repository.get_active_trip_for_vehicle(db, vehicle.id)
    if active is not None:
        return flash.redirect(f"/kniha-jizd/trips/{active.id}")

    return await render_page(
        request, "trip_start.html", user, db,
        vehicle=vehicle, last_trip=await repository.get_last_completed_trip(db, vehicle.id),
        error=None, warnings=[], form={}, ocr_suggestion=None, odometer_attachment_id=None,
    )


@vehicle_trips_router.post("/vehicles/{vehicle_id}/trips/start", dependencies=[Depends(verify_csrf)])
async def trip_start(
    request: Request,
    vehicle_id: uuid.UUID,
    odometer_photo: UploadFile | None = File(None),
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id, user=user)
    form = await request.form()
    start_km = _to_int(form.get("start_odometer_km"))
    fuel_level = _to_int(form.get("start_fuel_level"))

    stashed_id = _stashed_attachment_id(form)

    async def rerender(error: str | None, warnings: list, suggestion=None, status_code: int = 400,
                       attachment_id: uuid.UUID | None = None):
        return await render_page(
            request, "trip_start.html", user, db, status_code=status_code,
            vehicle=vehicle, last_trip=await repository.get_last_completed_trip(db, vehicle.id),
            error=error, warnings=warnings, ocr_suggestion=suggestion,
            odometer_attachment_id=attachment_id or stashed_id,
            form={"start_odometer_km": start_km, "start_fuel_level": fuel_level},
        )

    if start_km is None:
        return await rerender("Zadejte stav tachometru.", [])

    photo = await _read_photo(odometer_photo)

    # OCR je pomocník: když přečte něco jiného, než co člověk napsal,
    # zeptáme se. Když nepřečte nic (nebo není zapnuté), jede se dál.
    if photo is not None and "ocr" not in _confirmations(form):
        reading = await ocr.read_odometer(photo[2])
        if reading is not None and reading.value != start_km:
            try:
                stashed = await _stash_photo_for_confirmation(
                    db, vehicle_id=vehicle.id, kind="odometer_start", actor_id=user.id, photo=photo,
                )
            except (PhotoTooLarge, UnsupportedPhotoType) as error:
                return await rerender(f"Fotografii se nepodařilo uložit: {error}", [])
            return await rerender(None, [], suggestion=reading, status_code=200, attachment_id=stashed)

    try:
        trip = await service.start_trip(
            db, vehicle=vehicle, actor=user, start_odometer_km=start_km, start_fuel_level=fuel_level,
            confirmations=_confirmations(form), odometer_photo=photo,
            odometer_attachment_id=stashed_id,
        )
    except service.TripWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.TripApprovalRequired as error:
        # Vozidlo vyžaduje schválení - vrátit řidiče na kartu vozidla, kde
        # je formulář žádosti, ne ho nechat na slepém formuláři jízdy.
        return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "approval_needed")
    except service.TripError as error:
        return await rerender(str(error), [])
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return await rerender(f"Fotografii se nepodařilo uložit: {error}", [])

    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "trip_started")


# --- moje jízdy a detail ----------------------------------------------

@trips_router.get("/trips/mine")
async def my_trips(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "my_trips.html", user, db,
        active_trips=await repository.list_active_trips_for_user(db, user.id),
        trips=await repository.list_trips_for_user(db, user.id),
    )


@trips_router.get("/trips/{trip_id}")
async def trip_detail(
    request: Request,
    trip_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    codes = await get_user_permission_codes(db, user.id)
    return await render_page(
        request, "trip_detail.html", user, db,
        trip=trip, vehicle=trip.vehicle,
        can_end=service.can_end_trip(trip, user, codes, trip.vehicle),
        can_cancel=MANAGE in codes or MANAGE_ANY in codes,
        candidate_drivers=await repository.list_candidate_drivers(db, trip),
    )


# --- ukončení ---------------------------------------------------------

@trips_router.get("/trips/{trip_id}/end")
async def trip_end_form(
    request: Request,
    trip_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_end(db, trip, user)
    if trip.status != "active":
        return flash.redirect(f"/kniha-jizd/trips/{trip.id}")

    return await render_page(
        request, "trip_end.html", user, db,
        trip=trip, vehicle=trip.vehicle, purposes=TRIP_PURPOSES,
        error=None, warnings=[], form={}, ocr_suggestion=None, odometer_attachment_id=None,
    )


@trips_router.post("/trips/{trip_id}/end", dependencies=[Depends(verify_csrf)])
async def trip_end(
    request: Request,
    trip_id: uuid.UUID,
    odometer_photo: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_end(db, trip, user)

    form = await request.form()
    end_km = _to_int(form.get("end_odometer_km"))
    fuel_level = _to_int(form.get("end_fuel_level"))
    payload = {
        "end_odometer_km": end_km,
        "end_fuel_level": fuel_level,
        "purpose_code": str(form.get("purpose_code") or "").strip() or None,
        "purpose_text": str(form.get("purpose_text") or "").strip() or None,
        "route_text": str(form.get("route_text") or ""),
        "note": str(form.get("note") or "").strip() or None,
    }

    stashed_id = _stashed_attachment_id(form)

    async def rerender(error: str | None, warnings: list, suggestion=None, status_code: int = 400,
                       attachment_id: uuid.UUID | None = None):
        return await render_page(
            request, "trip_end.html", user, db, status_code=status_code,
            trip=trip, vehicle=trip.vehicle, purposes=TRIP_PURPOSES,
            error=error, warnings=warnings, ocr_suggestion=suggestion,
            odometer_attachment_id=attachment_id or stashed_id, form=payload,
        )

    if end_km is None:
        return await rerender("Zadejte konečný stav tachometru.", [])

    photo = await _read_photo(odometer_photo)
    if photo is not None and "ocr" not in _confirmations(form):
        reading = await ocr.read_odometer(photo[2])
        if reading is not None and reading.value != end_km:
            try:
                stashed = await _stash_photo_for_confirmation(
                    db, vehicle_id=trip.vehicle_id, kind="odometer_end", actor_id=user.id, photo=photo,
                )
            except (PhotoTooLarge, UnsupportedPhotoType) as error:
                return await rerender(f"Fotografii se nepodařilo uložit: {error}", [])
            return await rerender(None, [], suggestion=reading, status_code=200, attachment_id=stashed)

    try:
        await service.end_trip(
            db, trip=trip, actor=user, end_odometer_km=end_km, end_fuel_level=fuel_level,
            purpose_code=payload["purpose_code"], purpose_text=payload["purpose_text"],
            route_text=payload["route_text"], note=payload["note"],
            confirmations=_confirmations(form), odometer_photo=photo,
            odometer_attachment_id=stashed_id,
        )
    except service.TripWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.TripError as error:
        return await rerender(str(error), [])
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return await rerender(f"Fotografii se nepodařilo uložit: {error}", [])

    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "trip_ended")


# --- další řidiči, poznámky, zrušení ----------------------------------

@trips_router.post("/trips/{trip_id}/drivers", dependencies=[Depends(verify_csrf)])
async def trip_add_driver(
    trip_id: uuid.UUID,
    driver_id: uuid.UUID = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_end(db, trip, user)
    try:
        await service.add_driver(db, trip=trip, user_id=driver_id, actor=user)
    except service.TripError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "driver_added")


@trips_router.post("/trips/{trip_id}/drivers/{driver_id}/delete", dependencies=[Depends(verify_csrf)])
async def trip_remove_driver(
    trip_id: uuid.UUID,
    driver_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_end(db, trip, user)
    try:
        await service.remove_driver(db, trip=trip, user_id=driver_id, actor=user)
    except service.TripError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "driver_removed")


@trips_router.post("/trips/{trip_id}/notes", dependencies=[Depends(verify_csrf)])
async def trip_add_note(
    trip_id: uuid.UUID,
    text: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    try:
        await service.add_note(db, trip=trip, text=text, actor=user)
    except service.TripError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "note_added")


@trips_router.post("/trips/{trip_id}/cancel", dependencies=[Depends(verify_csrf)])
async def trip_cancel(
    trip_id: uuid.UUID,
    reason: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    if not ({MANAGE, MANAGE_ANY} & codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění rušit jízdy.")
    trip = await _load_trip(db, trip_id)
    try:
        await service.cancel_trip(db, trip=trip, reason=reason, actor=user)
    except service.TripError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "trip_cancelled")


# --- úprava časů jízdy (požadavek A) ------------------------------------

def _parse_local_dt(raw) -> datetime | None:
    """<input type="datetime-local"> posílá čas bez zóny - doplní se
    provozní, aby se uložil správný okamžik."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        return None
    return naive.replace(tzinfo=LOCAL_TZ) if naive.tzinfo is None else naive


async def _assert_can_edit_times(db: AsyncSession, trip: Trip, actor: User) -> None:
    """Časy opravuje ten, kdo jel, nebo správce vozidla či jízd. Stejný
    okruh jako u ukončení jízdy - kdo ji smí zavřít, smí i opravit, kdy
    se to stalo."""
    await _assert_can_end(db, trip, actor)


@trips_router.get("/trips/{trip_id}/times")
async def trip_times_form(
    request: Request,
    trip_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_edit_times(db, trip, user)
    return await render_page(
        request, "trip_times_edit.html", user, db,
        trip=trip, vehicle=trip.vehicle, error=None, warnings=[], form={},
    )


@trips_router.post("/trips/{trip_id}/times", dependencies=[Depends(verify_csrf)])
async def trip_times_edit(
    request: Request,
    trip_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip = await _load_trip(db, trip_id)
    await _assert_can_edit_times(db, trip, user)

    form = await request.form()
    payload = {
        "started_at": str(form.get("started_at") or ""),
        "ended_at": str(form.get("ended_at") or ""),
        "reason": str(form.get("reason") or ""),
    }

    async def rerender(error, warnings, status_code=400):
        return await render_page(
            request, "trip_times_edit.html", user, db, status_code=status_code,
            trip=trip, vehicle=trip.vehicle, error=error, warnings=warnings, form=payload,
        )

    started_at = _parse_local_dt(payload["started_at"])
    if started_at is None:
        return await rerender("Zadejte datum a čas zahájení.", [])

    try:
        await service.edit_times(
            db, trip=trip, actor=user, started_at=started_at,
            ended_at=_parse_local_dt(payload["ended_at"]), reason=payload["reason"],
            confirmations=_confirmations(form),
        )
    except service.TripWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.TripError as error:
        return await rerender(str(error), [])

    return flash.redirect(f"/kniha-jizd/trips/{trip.id}", "trip_times_edited")
