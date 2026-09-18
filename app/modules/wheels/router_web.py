"""Kola a pneumatiky - sady a přezutí."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.access import (
    MANAGE_ANY,
    MANAGE_OWN,
    MANAGE_PRIVATE,
    assert_vehicle_manage_access,
    assert_vehicle_visible,
    can_manage_vehicle,
)
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_any_permission
from app.core.photos import PhotoTooLarge, UnsupportedPhotoType
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import WHEEL_SEASONS, Vehicle
from app.modules.vehicles import repository as vehicles_repository
from app.modules.wheels import repository, service

wheels_router = APIRouter(tags=["wheels-web"])
vehicle_wheels_router = APIRouter(tags=["wheels-web"])


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User, codes: set[str]) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)
    return vehicle


def _to_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def _to_float(raw) -> float | None:
    text = str(raw or "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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


async def _read_photo(upload: UploadFile | None):
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    return (upload.filename, upload.content_type, data) if data else None


async def _page_context(db: AsyncSession, vehicle: Vehicle, codes: set[str], user: User, **extra) -> dict:
    sets = await repository.list_sets(db, vehicle.id)
    active = await repository.get_active_fitment(db, vehicle.id)
    return {
        "vehicle": vehicle,
        "wheel_sets": sets,
        "active_fitment": active,
        "fitments": await repository.list_fitments(db, vehicle.id),
        "seasons": WHEEL_SEASONS,
        "distances": {
            wheel_set.id: service.set_distance_km(wheel_set, vehicle.current_odometer_km)
            for wheel_set in sets
        },
        "can_manage": can_manage_vehicle(codes, vehicle, user),
        "today_iso": date.today().isoformat(),
        **extra,
    }


# --- přehled kol --------------------------------------------------------

@vehicle_wheels_router.get("/vehicles/{vehicle_id}/wheels")
async def vehicle_wheels(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    return await render_page(
        request, "wheels_list.html", user, db,
        **await _page_context(db, vehicle, codes, user, error=None, warnings=[], form={}),
    )


# --- nová sada ----------------------------------------------------------

@vehicle_wheels_router.post("/vehicles/{vehicle_id}/wheels/sets", dependencies=[Depends(verify_csrf)])
async def wheel_set_create(
    request: Request,
    vehicle_id: uuid.UUID,
    photo: UploadFile | None = File(None),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN, MANAGE_PRIVATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    assert_vehicle_manage_access(codes, vehicle, user)

    form = await request.form()
    payload = {
        "season": _text(form, "season") or "",
        "brand": _text(form, "brand"),
        "model": _text(form, "model"),
        "size": _text(form, "size"),
        "purchased_at": _text(form, "purchased_at"),
        "purchase_odometer_km": _text(form, "purchase_odometer_km"),
        "dot_code": _text(form, "dot_code"),
        "tread_depth_mm": _text(form, "tread_depth_mm"),
        "note": _text(form, "note"),
    }

    try:
        await service.create_set(
            db, vehicle=vehicle, actor=user, season=payload["season"], brand=payload["brand"],
            model=payload["model"], size=payload["size"],
            purchased_at=_to_date(payload["purchased_at"]),
            purchase_odometer_km=_to_int(payload["purchase_odometer_km"]),
            dot_code=payload["dot_code"], tread_depth_mm=_to_float(payload["tread_depth_mm"]),
            note=payload["note"], photo=await _read_photo(photo),
        )
    except (service.WheelError, PhotoTooLarge, UnsupportedPhotoType) as error:
        return await render_page(
            request, "wheels_list.html", user, db, status_code=400,
            **await _page_context(db, vehicle, codes, user, error=str(error), warnings=[], form=payload),
        )
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/wheels", "wheel_set_added")


# --- přezutí ------------------------------------------------------------

@vehicle_wheels_router.post("/vehicles/{vehicle_id}/wheels/fit", dependencies=[Depends(verify_csrf)])
async def wheels_fit(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN, MANAGE_PRIVATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    assert_vehicle_manage_access(codes, vehicle, user)

    form = await request.form()
    raw_set = _text(form, "wheel_set_id") or ""
    payload = {
        "wheel_set_id": raw_set,
        "fitted_at": _text(form, "fitted_at"),
        "odometer_km": _text(form, "odometer_km"),
        "note": _text(form, "note"),
    }
    confirmations = {value for value in form.getlist("confirm") if isinstance(value, str)}

    async def rerender(error, warnings, status_code=400):
        return await render_page(
            request, "wheels_list.html", user, db, status_code=status_code,
            **await _page_context(
                db, vehicle, codes, user, error=error, warnings=warnings, form=payload,
            ),
        )

    try:
        wheel_set = await repository.get_set(db, uuid.UUID(raw_set))
    except ValueError:
        return await rerender("Vyberte sadu.", [])
    if wheel_set is None:
        return await rerender("Sada nebyla nalezena.", [])

    fitted_at = _to_date(payload["fitted_at"])
    odometer_km = _to_int(payload["odometer_km"])
    if fitted_at is None or odometer_km is None:
        return await rerender("Vyplňte datum i stav tachometru.", [])

    try:
        await service.change_wheels(
            db, vehicle=vehicle, actor=user, wheel_set=wheel_set,
            fitted_at=fitted_at, odometer_km=odometer_km, note=payload["note"],
            confirmations=confirmations,
        )
    except service.WheelWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.WheelError as error:
        return await rerender(str(error), [])

    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/wheels", "wheels_fitted")


@vehicle_wheels_router.post("/vehicles/{vehicle_id}/wheels/remove", dependencies=[Depends(verify_csrf)])
async def wheels_remove(
    vehicle_id: uuid.UUID,
    removed_at: str = Form(...),
    odometer_km: str = Form(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN, MANAGE_PRIVATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    assert_vehicle_manage_access(codes, vehicle, user)

    parsed_date = _to_date(removed_at)
    parsed_km = _to_int(odometer_km)
    if parsed_date is None or parsed_km is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vyplňte datum i stav tachometru.")

    try:
        await service.remove_wheels(
            db, vehicle=vehicle, actor=user, removed_at=parsed_date, odometer_km=parsed_km,
        )
    except service.WheelError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/wheels", "wheels_removed")


# --- úprava a vyřazení sady --------------------------------------------

@wheels_router.post("/wheel-sets/{wheel_set_id}/edit", dependencies=[Depends(verify_csrf)])
async def wheel_set_update(
    request: Request,
    wheel_set_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN, MANAGE_PRIVATE)),
    db: AsyncSession = Depends(get_db),
):
    wheel_set = await repository.get_set(db, wheel_set_id)
    if wheel_set is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sada nebyla nalezena.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, wheel_set.vehicle, user)
    assert_vehicle_manage_access(codes, wheel_set.vehicle, user)

    form = await request.form()
    try:
        await service.update_set(
            db, wheel_set=wheel_set, actor=user,
            brand=_text(form, "brand"), model=_text(form, "model"), size=_text(form, "size"),
            dot_code=_text(form, "dot_code"), tread_depth_mm=_to_float(_text(form, "tread_depth_mm")),
            note=_text(form, "note"),
        )
    except service.WheelError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/vehicles/{wheel_set.vehicle_id}/wheels", "wheel_set_updated")


@wheels_router.post("/wheel-sets/{wheel_set_id}/delete", dependencies=[Depends(verify_csrf)])
async def wheel_set_delete(
    wheel_set_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN, MANAGE_PRIVATE)),
    db: AsyncSession = Depends(get_db),
):
    wheel_set = await repository.get_set(db, wheel_set_id)
    if wheel_set is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sada nebyla nalezena.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, wheel_set.vehicle, user)
    assert_vehicle_manage_access(codes, wheel_set.vehicle, user)

    vehicle_id = wheel_set.vehicle_id
    try:
        await service.delete_set(db, wheel_set=wheel_set, actor=user)
    except service.WheelError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle_id}/wheels", "wheel_set_deleted")
