"""Servisní historie (Etapa 6, zadání 17)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings, flash
from app.core.access import (
    MANAGE_ANY,
    MANAGE_OWN,
    assert_vehicle_manage_access,
    assert_vehicle_visible,
    can_manage_vehicle,
)
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_any_permission
from app.core.fleet_status import oil_status
from app.core.photos import PhotoTooLarge, UnsupportedPhotoType
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import SERVICE_TYPES, Vehicle
from app.core.documents import DocumentTooLarge, UnsupportedDocumentType
from app.modules.documents import repository as documents_repository
from app.modules.documents import service as documents_service
from app.modules.services import repository, service
from app.modules.vehicles import repository as vehicles_repository

services_router = APIRouter(tags=["services-web"])
vehicle_services_router = APIRouter(tags=["services-web"])

VIEW = "fleet.vehicle.view"


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User, codes: set[str]) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)
    return vehicle


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


async def _read_file(upload: UploadFile | None):
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    return (upload.filename, upload.content_type, data) if data else None


# --- přehled servisu u vozidla ----------------------------------------

@vehicle_services_router.get("/vehicles/{vehicle_id}/services")
async def vehicle_services(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    thresholds = await app_settings.get_all(db)

    return await render_page(
        request, "services_list.html", user, db,
        vehicle=vehicle,
        records=await repository.list_for_vehicle(db, vehicle.id),
        service_documents={
            record.id: await documents_repository.list_for_service(db, record.id)
            for record in await repository.list_for_vehicle(db, vehicle.id)
        },
        totals=await repository.totals_for_vehicle(db, vehicle.id),
        oil=oil_status(vehicle, thresholds),
        service_types=SERVICE_TYPES,
        can_manage=can_manage_vehicle(codes, vehicle, user),
    )


# --- nový záznam ------------------------------------------------------

@vehicle_services_router.get("/vehicles/{vehicle_id}/services/new")
async def service_new_form(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    assert_vehicle_manage_access(codes, vehicle, user)

    return await render_page(
        request, "service_form.html", user, db,
        vehicle=vehicle, service_types=SERVICE_TYPES, error=None, warnings=[],
        form={
            "service_date": date.today().isoformat(),
            # Stav km se předvyplní aktuálním - servis se obvykle zapisuje
            # hned po návratu z něj.
            "odometer_km": str(vehicle.current_odometer_km),
        },
    )


@vehicle_services_router.post("/vehicles/{vehicle_id}/services/new", dependencies=[Depends(verify_csrf)])
async def service_create(
    request: Request,
    vehicle_id: uuid.UUID,
    invoice: UploadFile | None = File(None),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    assert_vehicle_manage_access(codes, vehicle, user)

    form = await request.form()
    payload = {
        "service_date": _text(form, "service_date"),
        "service_type": _text(form, "service_type") or "",
        "description": _text(form, "description") or "",
        "odometer_km": _text(form, "odometer_km"),
        "supplier": _text(form, "supplier"),
        "price_czk": _text(form, "price_czk"),
        "note": _text(form, "note"),
        "update_oil_interval": form.get("update_oil_interval") is not None,
    }
    confirmations = {value for value in form.getlist("confirm") if isinstance(value, str)}

    async def rerender(error, warnings, status_code=400):
        return await render_page(
            request, "service_form.html", user, db, status_code=status_code,
            vehicle=vehicle, service_types=SERVICE_TYPES,
            error=error, warnings=warnings, form=payload,
        )

    try:
        record = await service.add_service(
            db, vehicle=vehicle, actor=user,
            service_date=_to_date(payload["service_date"]),
            service_type=payload["service_type"],
            description=payload["description"],
            odometer_km=_to_int(payload["odometer_km"]),
            supplier=payload["supplier"],
            price_czk=_to_float(payload["price_czk"]),
            note=payload["note"],
            confirmations=confirmations,
            update_oil_interval=payload["update_oil_interval"],
            invoice=await _read_file(invoice),
        )
    except service.ServiceWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except service.ServiceError as error:
        return await rerender(str(error), [])
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        return await rerender(f"Fakturu se nepodařilo uložit: {error}", [])

    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/services", "service_added")


# --- přílohy a mazání -------------------------------------------------

@services_router.post("/services/{service_id}/attachments", dependencies=[Depends(verify_csrf)])
async def service_add_attachment(
    service_id: uuid.UUID,
    photo: UploadFile = File(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    record = await repository.get(db, service_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Záznam nebyl nalezen.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, record.vehicle, user)
    assert_vehicle_manage_access(codes, record.vehicle, user)

    data = await _read_file(photo)
    if data is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vyberte soubor.")
    try:
        await service.add_attachment(db, record=record, actor=user, photo=data)
    except (PhotoTooLarge, UnsupportedPhotoType) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/vehicles/{record.vehicle_id}/services", "photo_added")


@services_router.post("/services/{service_id}/documents", dependencies=[Depends(verify_csrf)])
async def service_add_document(
    service_id: uuid.UUID,
    document: UploadFile = File(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    """Doklad k servisu jako PLNOHODNOTNÝ dokument, tedy i PDF.

    Fotografie faktury jde nahrát přes /attachments (obrázková pipeline
    se zmenšováním), ale faktura přijatá e-mailem bývá PDF - to přes
    obrázkovou cestu neprojde. Používá se stejné úložiště jako u
    dokumentů vozidla: umí PDF, ověřuje magické bajty, dává souborům
    náhodná jména a má autorizované stahování."""
    record = await repository.get(db, service_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Záznam nebyl nalezen.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, record.vehicle, user)
    assert_vehicle_manage_access(codes, record.vehicle, user)

    data = await document.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vyberte soubor.")

    try:
        await documents_service.add_document(
            db, vehicle=record.vehicle, actor=user, doc_type="faktura",
            title=f"Doklad – {document.filename}", valid_from=None, valid_to=None, note=None,
            filename=document.filename or "doklad", content_type=document.content_type, data=data,
            service_id=record.id,
        )
    except (documents_service.DocumentError, DocumentTooLarge, UnsupportedDocumentType) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return flash.redirect(f"/kniha-jizd/vehicles/{record.vehicle_id}/services", "document_added")


@services_router.post("/services/{service_id}/delete", dependencies=[Depends(verify_csrf)])
async def service_delete(
    service_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    record = await repository.get(db, service_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Záznam nebyl nalezen.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, record.vehicle, user)
    assert_vehicle_manage_access(codes, record.vehicle, user)

    vehicle_id = record.vehicle_id
    await service.delete_service(db, record=record, actor=user)
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle_id}/services", "service_deleted")
