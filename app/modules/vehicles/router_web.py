"""Vozidla - seznam, karta vozidla, QR kód, fotografie (zadání 5/6/7).

Karta vozidla je zároveň obrazovka, na kterou řidič dorazí po načtení QR
(zadání 7), takže musí být čitelná na mobilu a musí na první pohled
ukázat stav km, nádrž/baterii, termíny se semaforem a otevřené závady.
"""
import io
import uuid
from datetime import date

import qrcode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings, flash
from app.core.access import (
    MANAGE_ANY,
    MANAGE_OWN,
    assert_vehicle_manage_access,
    assert_vehicle_visible,
    can_manage_vehicle,
    visible_vehicles_condition,
)
from app.core.config import get_settings
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_any_permission, require_permission
from app.core.fleet_status import vehicle_deadlines, worst_level
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import FUEL_TYPES, VEHICLE_STATUSES, VEHICLE_TYPES, Vehicle
from app.modules.approvals import repository as approvals_repository
from app.modules.approvals import service as approvals_service
from app.modules.defects import repository as defects_repository
from app.modules.documents import repository as documents_repository
from app.modules.expenses import repository as expenses_repository
from app.modules.fuelings import repository as fuelings_repository
from app.modules.services import repository as services_repository
from app.modules.wheels import repository as wheels_repository
from app.modules.wheels import service as wheels_service
from app.modules.trips import repository as trips_repository
from app.modules.vehicles import repository, service
from app.modules.vehicles.schemas import VehicleCreate, VehicleUpdate

vehicles_router = APIRouter(tags=["vehicles-web"])
attachments_router = APIRouter(tags=["attachments-web"])
qr_landing_router = APIRouter(tags=["qr-web"])

VIEW = "fleet.vehicle.view"
CREATE = "fleet.vehicle.create"


# --- pomocné ---------------------------------------------------------

async def _load_vehicle(
    db: AsyncSession, vehicle_id: uuid.UUID, *, codes: set[str] | None = None, user: User | None = None,
) -> Vehicle:
    """Načte vozidlo a - když dostane, kdo se ptá - rovnou ověří, že ho
    ten člověk vůbec smí vidět (požadavek D)."""
    vehicle = await repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    if codes is not None and user is not None:
        assert_vehicle_visible(codes, vehicle, user)
    return vehicle


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() or None if value else None


def _to_int(value: str | None) -> int | None:
    value = _blank_to_none(value)
    return int(value) if value is not None else None


def _to_float(value: str | None) -> float | None:
    value = _blank_to_none(value)
    return float(value.replace(",", ".")) if value is not None else None


def _to_date(value: str | None) -> date | None:
    value = _blank_to_none(value)
    return date.fromisoformat(value) if value is not None else None


def _to_uuid(value: str | None) -> uuid.UUID | None:
    value = _blank_to_none(value)
    return uuid.UUID(value) if value is not None else None


async def _form_payload(request: Request) -> dict:
    """Prázdný <input> posílá "" - do schématu jde None, ne prázdný
    řetězec, aby "nevyplněno" bylo v databázi jedním stavem."""
    form = await request.form()

    def get(name: str) -> str | None:
        raw = form.get(name)
        return raw if isinstance(raw, str) else None

    return {
        "internal_code": (get("internal_code") or "").strip(),
        "license_plate": (get("license_plate") or "").strip(),
        "brand": (get("brand") or "").strip(),
        "model": (get("model") or "").strip(),
        "vin": _blank_to_none(get("vin")),
        "year_of_manufacture": _to_int(get("year_of_manufacture")),
        "vehicle_type": get("vehicle_type") or "osobni",
        "fuel_type": _blank_to_none(get("fuel_type")),
        "tank_capacity_l": _to_float(get("tank_capacity_l")),
        "battery_capacity_kwh": _to_float(get("battery_capacity_kwh")),
        "responsible_user_id": _to_uuid(get("responsible_user_id")),
        "status": get("status") or "available",
        "is_active": get("is_active") is not None,
        "approval_required": get("approval_required") is not None,
        "visibility": get("visibility") or "all",
        "stk_valid_until": _to_date(get("stk_valid_until")),
        "vignette_valid_until": _to_date(get("vignette_valid_until")),
        "insurance_company": _blank_to_none(get("insurance_company")),
        "insurance_policy_number": _blank_to_none(get("insurance_policy_number")),
        "insurance_valid_until": _to_date(get("insurance_valid_until")),
        "last_oil_change_at": _to_date(get("last_oil_change_at")),
        "last_oil_change_km": _to_int(get("last_oil_change_km")),
        "oil_interval_km": _to_int(get("oil_interval_km")),
        "oil_interval_months": _to_int(get("oil_interval_months")),
        "notes": _blank_to_none(get("notes")),
    }


async def _form_context(db: AsyncSession, **extra) -> dict:
    return {
        "users": await repository.list_active_users(db),
        "vehicle_types": VEHICLE_TYPES,
        "fuel_types": FUEL_TYPES,
        "vehicle_statuses": VEHICLE_STATUSES,
        **extra,
    }


# --- seznam ----------------------------------------------------------

@vehicles_router.get("/vehicles")
async def vehicles_list(
    request: Request,
    only: str = "",
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    thresholds = await app_settings.get_all(db)
    vehicles = (
        await repository.list_vehicles_for_responsible_user(db, user.id)
        if only == "mine"
        else await repository.list_vehicles(db, visible_to=visible_vehicles_condition(codes, user))
    )
    rows = [
        {
            "vehicle": vehicle,
            "deadlines": vehicle_deadlines(vehicle, thresholds),
            "worst": worst_level(vehicle_deadlines(vehicle, thresholds)),
        }
        for vehicle in vehicles
    ]
    return await render_page(
        request, "vehicles_list.html", user, db,
        rows=rows, only=only, can_create=CREATE in codes,
        busy_vehicle_ids=await trips_repository.busy_vehicle_ids(db),
    )


# --- založení -------------------------------------------------------
# Pozor na pořadí: /vehicles/new musí být registrované PŘED
# /vehicles/{vehicle_id}, jinak se "new" zkusí přečíst jako UUID.

@vehicles_router.get("/vehicles/new")
async def vehicle_new_form(
    request: Request,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "vehicle_form.html", user, db,
        **await _form_context(db, vehicle=None, error=None, form={}),
    )


@vehicles_router.post("/vehicles/new", dependencies=[Depends(verify_csrf)])
async def vehicle_create(
    request: Request,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    payload = await _form_payload(request)
    form = await request.form()
    payload["current_odometer_km"] = _to_int(str(form.get("current_odometer_km") or "")) or 0
    payload["current_fuel_level"] = _to_int(str(form.get("current_fuel_level") or ""))
    try:
        data = VehicleCreate(**payload)
        vehicle = await service.create_vehicle(db, data, user.id)
    except (ValueError, service.DuplicateInternalCode) as exc:
        return await render_page(
            request, "vehicle_form.html", user, db, status_code=400,
            **await _form_context(db, vehicle=None, error=_error_text(exc), form=payload),
        )
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "vehicle_created")


# --- karta vozidla ---------------------------------------------------

@vehicles_router.get("/vehicles/{vehicle_id}")
async def vehicle_detail(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, codes=codes, user=user)
    thresholds = await app_settings.get_all(db)
    return await render_page(
        request, "vehicle_detail.html", user, db,
        vehicle=vehicle,
        deadlines=vehicle_deadlines(vehicle, thresholds),
        assignments=await repository.list_assignments(db, vehicle.id),
        can_manage=can_manage_vehicle(codes, vehicle, user),
        # Jestli je vozidlo zrovna vypůjčené, se odvozuje z otevřené jízdy,
        # ne z uloženého příznaku (ten by se mohl rozejít se skutečností).
        active_trip=await trips_repository.get_active_trip_for_vehicle(db, vehicle.id),
        recent_trips=await trips_repository.list_trips_for_vehicle(db, vehicle.id, limit=5),
        can_start_trip="fleet.trip.create" in codes,
        # Otevřené závady se ukazují hned u stavu vozidla - řidič, který
        # k autu přijde, je musí vidět dřív, než vyjede (zadání 7/16).
        open_defects=await defects_repository.list_for_vehicle(db, vehicle.id, open_only=True),
        # Schvalování (požadavek B): buď má řidič platné schválení, nebo
        # čekající žádost, nebo se mu nabídne formulář.
        needs_approval=approvals_service.needs_approval(vehicle, user, codes),
        my_approval=await approvals_repository.get_usable_approval(
            db, vehicle_id=vehicle.id, requester_id=user.id,
        ),
        my_pending_request=await approvals_repository.get_pending_for(
            db, vehicle_id=vehicle.id, requester_id=user.id,
        ),
        can_report_defect="fleet.defect.report" in codes,
        recent_fuelings=await fuelings_repository.list_for_vehicle(db, vehicle.id, limit=5),
        fueling_totals=await fuelings_repository.totals_for_vehicle(db, vehicle.id),
        service_totals=await services_repository.totals_for_vehicle(db, vehicle.id),
        last_services=await services_repository.list_for_vehicle(db, vehicle.id),
        documents=await documents_repository.list_for_vehicle(db, vehicle.id),
        active_fitment=await wheels_repository.get_active_fitment(db, vehicle.id),
        wheels_service=wheels_service,
        expense_totals=await expenses_repository.combined_totals(db, vehicle.id),
        recent_expenses=await expenses_repository.list_for_vehicle(db, vehicle.id, limit=3),
    )


# --- úprava -----------------------------------------------------------

@vehicles_router.get("/vehicles/{vehicle_id}/edit")
async def vehicle_edit_form(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    return await render_page(
        request, "vehicle_form.html", user, db,
        **await _form_context(db, vehicle=vehicle, error=None, form={}),
    )


@vehicles_router.post("/vehicles/{vehicle_id}/edit", dependencies=[Depends(verify_csrf)])
async def vehicle_update(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    payload = await _form_payload(request)
    try:
        data = VehicleUpdate(**payload)
        await service.update_vehicle(db, vehicle, data, user.id)
    except (ValueError, service.DuplicateInternalCode) as exc:
        return await render_page(
            request, "vehicle_form.html", user, db, status_code=400,
            **await _form_context(db, vehicle=vehicle, error=_error_text(exc), form=payload),
        )
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "vehicle_updated")


def _error_text(exc: Exception) -> str:
    """Pydantic ValidationError vrací seznam chyb - řidiči ukážeme jen
    první srozumitelnou větu, ne celý dump."""
    if hasattr(exc, "errors"):
        problems = exc.errors()
        if problems:
            first = problems[0]
            field = ".".join(str(part) for part in first.get("loc", ())) or "formulář"
            message = first.get("msg", "").removeprefix("Value error, ")
            return f"{field}: {message}"
    return str(exc)


# --- administrativní oprava tachometru --------------------------------

@vehicles_router.post("/vehicles/{vehicle_id}/odometer", dependencies=[Depends(verify_csrf)])
async def vehicle_correct_odometer(
    request: Request,
    vehicle_id: uuid.UUID,
    new_km: int = Form(...),
    reason: str = Form(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    try:
        await service.correct_odometer(db, vehicle, new_km=new_km, reason=reason, actor_id=user.id)
    except service.OdometerCorrectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "odometer_corrected")


# --- QR kód -----------------------------------------------------------

def _qr_url(request: Request, vehicle: Vehicle) -> str:
    """Absolutní URL, kterou nese nálepka na vozidle. Bere se z requestu,
    takže lokálně vznikne lokální odkaz a na produkci produkční - proto
    uvicorn běží s --proxy-headers (viz docker/Dockerfile)."""
    return str(request.base_url).rstrip("/") + f"/kniha-jizd/v/{vehicle.qr_token}"


@vehicles_router.get("/vehicles/{vehicle_id}/qr")
async def vehicle_qr_print(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    return await render_page(
        request, "vehicle_qr_print.html", user, db, vehicle=vehicle, qr_url=_qr_url(request, vehicle),
    )


@vehicles_router.get("/vehicles/{vehicle_id}/qr.png")
async def vehicle_qr_png(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    image = qrcode.make(_qr_url(request, vehicle), box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@vehicles_router.get("/scan")
async def scan_qr(
    request: Request,
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(request, "scan_qr.html", user, db)


@qr_landing_router.get("/v/{qr_token}")
async def qr_landing(
    qr_token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cíl nálepky na vozidle. Token sám o sobě nic neodemyká - stránka
    vozidla je za přihlášením jako každá jiná (zadání 30). Nepřihlášeného
    uživatele odsud 401 pošle na login a ten ho po přihlášení vrátí
    přesně sem, ne na dashboard."""
    vehicle = await repository.get_vehicle_by_qr_token(db, qr_token)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Neplatný QR kód vozidla.")
    # Nálepka na autě nesmí obejít viditelnost (požadavek D): kdo vozidlo
    # nevidí v seznamu, nedostane se k němu ani načtením QR kódu.
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, vehicle, user)
    return RedirectResponse(url=f"/kniha-jizd/vehicles/{vehicle.id}", status_code=303)


# --- fotografie vozidla ------------------------------------------------

@vehicles_router.post("/vehicles/{vehicle_id}/photos", dependencies=[Depends(verify_csrf)])
async def vehicle_add_photo(
    request: Request,
    vehicle_id: uuid.UUID,
    photo: UploadFile = File(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    try:
        await service.add_attachment(
            db, vehicle_id=vehicle.id, kind="vehicle_photo", original_filename=photo.filename or "photo.jpg",
            content_type=photo.content_type, data=await photo.read(), actor_id=user.id,
        )
    except (service.PhotoTooLarge, service.UnsupportedPhotoType) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "photo_added")


@vehicles_router.post("/vehicles/{vehicle_id}/photos/{attachment_id}/delete", dependencies=[Depends(verify_csrf)])
async def vehicle_delete_photo(
    vehicle_id: uuid.UUID,
    attachment_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _load_vehicle(db, vehicle_id)
    assert_vehicle_manage_access(await get_user_permission_codes(db, user.id), vehicle, user)
    attachment = await repository.get_attachment(db, attachment_id)
    # Kontrola, že příloha patří k TOMUTO vozidlu - jinak by šlo cizí
    # fotografii smazat přes vozidlo, které spravuji (IDOR, zadání 30).
    if attachment is None or attachment.vehicle_id != vehicle.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fotografie nebyla nalezena.")
    await service.delete_attachment(db, attachment, user.id)
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}", "photo_deleted")


# --- výdej souborů fotografií ------------------------------------------

@attachments_router.get("/attachments/{attachment_id}/{variant}")
async def attachment_file(
    attachment_id: uuid.UUID,
    variant: str,
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Jediná cesta k nahranému souboru. Adresář s fotografiemi není
    servírovaný staticky - každé stažení projde přihlášením a kontrolou
    oprávnění, takže neexistuje uhodnutelná veřejná URL (zadání 24/30)."""
    if variant not in ("thumb", "full"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Neznámá varianta fotografie.")
    attachment = await repository.get_attachment(db, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fotografie nebyla nalezena.")

    filename = attachment.thumbnail_path if variant == "thumb" else attachment.full_path
    path = get_settings().photos_path / filename
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Soubor fotografie chybí na disku.")
    return Response(
        content=path.read_bytes(),
        media_type=attachment.mime_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
