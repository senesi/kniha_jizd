"""Závady (Etapa 4, zadání 16).

Nahlášení musí být rychlé i z telefonu u auta - popis, priorita,
volitelně fotka, hotovo. Řešení závady (převzetí, uzavření) je naopak
práce pro odpovědnou osobu a smí být upovídanější.
"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.access import assert_vehicle_visible, visible_vehicles_condition
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_permission
from app.core.photos import PhotoTooLarge, UnsupportedPhotoType
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import DEFECT_PRIORITIES, DEFECT_STATUSES, VehicleDefect
from app.modules.defects import repository, service
from app.modules.vehicles import repository as vehicles_repository

defects_router = APIRouter(tags=["defects-web"])
vehicle_defects_router = APIRouter(tags=["defects-web"])

REPORT = "fleet.defect.report"


async def _load_defect(db: AsyncSession, defect_id: uuid.UUID, *, codes: set[str], user: User) -> VehicleDefect:
    defect = await repository.get(db, defect_id)
    if defect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Závada nebyla nalezena.")
    # Skryté vozidlo znamená skryté i jeho závady (požadavek D).
    assert_vehicle_visible(codes, defect.vehicle, user)
    return defect


async def _read_photo(upload: UploadFile | None):
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if not data:
        return None
    return upload.filename, upload.content_type, data


# --- nahlášení --------------------------------------------------------

@vehicle_defects_router.get("/vehicles/{vehicle_id}/defects/new")
async def defect_new_form(
    request: Request,
    vehicle_id: uuid.UUID,
    trip: str = "",
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)

    return await render_page(
        request, "defect_form.html", user, db,
        vehicle=vehicle, priorities=DEFECT_PRIORITIES, trip_id=trip, error=None, form={},
    )


@vehicle_defects_router.post("/vehicles/{vehicle_id}/defects/new", dependencies=[Depends(verify_csrf)])
async def defect_create(
    request: Request,
    vehicle_id: uuid.UUID,
    photo: UploadFile | None = File(None),
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)

    form = await request.form()
    payload = {
        "description": str(form.get("description") or ""),
        "priority": str(form.get("priority") or "normal"),
    }
    raw_trip = str(form.get("trip_id") or "").strip()
    trip_id = None
    if raw_trip:
        try:
            trip_id = uuid.UUID(raw_trip)
        except ValueError:
            trip_id = None

    try:
        defect = await service.report_defect(
            db, vehicle=vehicle, actor=user, description=payload["description"],
            priority=payload["priority"], trip_id=trip_id, photo=await _read_photo(photo),
        )
    except (service.DefectError, PhotoTooLarge, UnsupportedPhotoType) as error:
        return await render_page(
            request, "defect_form.html", user, db, status_code=400,
            vehicle=vehicle, priorities=DEFECT_PRIORITIES, trip_id=raw_trip,
            error=str(error), form=payload,
        )

    return flash.redirect(f"/kniha-jizd/defects/{defect.id}", "defect_reported")


# --- přehled ----------------------------------------------------------

@defects_router.get("/defects")
async def defects_list(
    request: Request,
    status_filter: str = "open",
    priority: str = "",
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    defects = await repository.list_all(
        db, status=status_filter, priority=priority,
        visible_to=visible_vehicles_condition(codes, user),
    )
    return await render_page(
        request, "defects_list.html", user, db,
        defects=defects, status_filter=status_filter, priority=priority,
        statuses=DEFECT_STATUSES, priorities=DEFECT_PRIORITIES,
    )


@defects_router.get("/defects/{defect_id}")
async def defect_detail(
    request: Request,
    defect_id: uuid.UUID,
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    defect = await _load_defect(db, defect_id, codes=codes, user=user)
    return await render_page(
        request, "defect_detail.html", user, db,
        defect=defect, vehicle=defect.vehicle,
        statuses=DEFECT_STATUSES, priorities=DEFECT_PRIORITIES,
        can_manage=service.can_manage_defect(defect.vehicle, user, codes),
    )


# --- řešení -----------------------------------------------------------

@defects_router.post("/defects/{defect_id}/status", dependencies=[Depends(verify_csrf)])
async def defect_change_status(
    defect_id: uuid.UUID,
    new_status: str = Form(...),
    note: str = Form(""),
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    defect = await _load_defect(db, defect_id, codes=codes, user=user)
    if not service.can_manage_defect(defect.vehicle, user, codes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Závadu řeší odpovědná osoba vozidla nebo administrátor.",
        )
    try:
        await service.change_status(db, defect=defect, actor=user, new_status=new_status, note=note)
    except service.DefectError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/defects/{defect.id}", "defect_updated")


@defects_router.post("/defects/{defect_id}/priority", dependencies=[Depends(verify_csrf)])
async def defect_change_priority(
    defect_id: uuid.UUID,
    priority: str = Form(...),
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    defect = await _load_defect(db, defect_id, codes=codes, user=user)
    if not service.can_manage_defect(defect.vehicle, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění měnit prioritu.")
    try:
        await service.change_priority(db, defect=defect, actor=user, priority=priority)
    except service.DefectError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/defects/{defect.id}", "defect_updated")


@defects_router.post("/defects/{defect_id}/photos", dependencies=[Depends(verify_csrf)])
async def defect_add_photo(
    defect_id: uuid.UUID,
    photo: UploadFile = File(...),
    user: User = Depends(require_permission(REPORT)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    defect = await _load_defect(db, defect_id, codes=codes, user=user)
    # Fotku smí přidat i ten, kdo závadu nahlásil - často ji dofotí až po
    # odeslání, když si všimne dalšího poškození.
    if defect.reported_by != user.id and not service.can_manage_defect(defect.vehicle, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění k této závadě.")

    data = await _read_photo(photo)
    if data is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vyberte fotografii.")
    try:
        await service.add_photo(db, defect=defect, actor=user, photo=data)
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/defects/{defect.id}", "photo_added")
