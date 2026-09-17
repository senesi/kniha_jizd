import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.config import get_settings
from app.core.photos import (
    ALLOWED_PHOTO_EXTENSIONS,
    MAX_PHOTO_UPLOAD_BYTES,
    PhotoTooLarge,
    UnsupportedPhotoType,
    process_upload,
    save_photo_files,
)
from app.models.fleet import Attachment, Vehicle, VehicleAssignment
from app.modules.vehicles import repository
from app.modules.vehicles.schemas import VehicleCreate, VehicleUpdate

MODULE = "vehicles"

__all__ = [
    "ALLOWED_PHOTO_EXTENSIONS", "MAX_PHOTO_UPLOAD_BYTES", "PhotoTooLarge", "UnsupportedPhotoType",
    "DuplicateInternalCode", "OdometerCorrectionError",
    "generate_qr_token", "create_vehicle", "update_vehicle", "correct_odometer",
    "add_attachment", "link_attachment_to_trip", "link_attachment_to_fueling", "delete_attachment",
]


class DuplicateInternalCode(Exception):
    pass


class OdometerCorrectionError(Exception):
    pass


def generate_qr_token() -> str:
    """Short, random, unguessable - deliberately not the DB id (zadání 6:
    "QR nesmí obsahovat citlivé údaje. Používat náhodný/neodhadnutelný
    token"). ~48 bits of entropy; the worst case of guessing one is
    reaching a vehicle card that still requires a login, not bypassing
    authentication."""
    return secrets.token_urlsafe(6)


async def _unique_qr_token(db: AsyncSession) -> str:
    for _ in range(10):
        token = generate_qr_token()
        if not await repository.qr_token_exists(db, token):
            return token
    raise RuntimeError("Could not generate a unique QR token after 10 attempts")


async def create_vehicle(db: AsyncSession, data: VehicleCreate, actor_id: uuid.UUID) -> Vehicle:
    if await repository.internal_code_exists(db, data.internal_code):
        raise DuplicateInternalCode(f"Vozidlo s interním označením „{data.internal_code}“ už existuje.")

    qr_token = await _unique_qr_token(db)
    payload = data.model_dump()
    vehicle = Vehicle(**payload, qr_token=qr_token, state_updated_at=datetime.now(timezone.utc))
    db.add(vehicle)
    await db.flush()

    if vehicle.responsible_user_id is not None:
        db.add(VehicleAssignment(
            vehicle_id=vehicle.id, user_id=vehicle.responsible_user_id, created_by=actor_id,
        ))

    await log_action(
        db, user_id=actor_id, action="create", module=MODULE, entity_type="vehicle", entity_id=str(vehicle.id),
        after_data=data.model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def update_vehicle(db: AsyncSession, vehicle: Vehicle, data: VehicleUpdate, actor_id: uuid.UUID) -> Vehicle:
    if await repository.internal_code_exists(db, data.internal_code, exclude_id=vehicle.id):
        raise DuplicateInternalCode(f"Vozidlo s interním označením „{data.internal_code}“ už existuje.")

    changes = data.model_dump()
    before = {k: getattr(vehicle, k) for k in changes}
    previous_responsible = vehicle.responsible_user_id

    for field, value in changes.items():
        setattr(vehicle, field, value)
    await db.flush()

    # Responsibility handover is history, not just a changed column
    # (zadání 26) - close the open assignment row and open a new one.
    if previous_responsible != vehicle.responsible_user_id:
        await _record_assignment_change(db, vehicle, actor_id)

    await log_action(
        db, user_id=actor_id, action="update", module=MODULE, entity_type="vehicle", entity_id=str(vehicle.id),
        before_data=_jsonable(before), after_data=_jsonable(changes),
    )
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def _record_assignment_change(db: AsyncSession, vehicle: Vehicle, actor_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    open_rows = await db.execute(
        select(VehicleAssignment).where(
            VehicleAssignment.vehicle_id == vehicle.id, VehicleAssignment.valid_to.is_(None)
        )
    )
    for row in open_rows.scalars().all():
        row.valid_to = now
    if vehicle.responsible_user_id is not None:
        db.add(VehicleAssignment(
            vehicle_id=vehicle.id, user_id=vehicle.responsible_user_id, valid_from=now, created_by=actor_id,
        ))
    await db.flush()


def _jsonable(values: dict) -> dict:
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else (str(v) if isinstance(v, uuid.UUID) else v))
        for k, v in values.items()
    }


async def correct_odometer(
    db: AsyncSession, vehicle: Vehicle, *, new_km: int, reason: str, actor_id: uuid.UUID
) -> Vehicle:
    """The ONLY way the stored odometer can move other than by closing a
    trip - and the only way it can ever move DOWN (zadání 5/32: "Nikdy
    nesmí být možné běžnou jízdou nastavit konečný stav km nižší než
    předchozí platný stav bez explicitního administrativního zásahu").
    Requires a reason, and is always audited with the old and new value."""
    if new_km < 0:
        raise OdometerCorrectionError("Stav tachometru nemůže být záporný.")
    if not reason.strip():
        raise OdometerCorrectionError("Zdůvodnění administrativní opravy je povinné.")

    before_km = vehicle.current_odometer_km
    vehicle.current_odometer_km = new_km
    vehicle.state_updated_at = datetime.now(timezone.utc)
    await db.flush()
    await log_action(
        db, user_id=actor_id, action="odometer_correction", module=MODULE, entity_type="vehicle",
        entity_id=str(vehicle.id),
        before_data={"current_odometer_km": before_km},
        after_data={"current_odometer_km": new_km, "reason": reason.strip()},
    )
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def add_attachment(
    db: AsyncSession, *, vehicle_id: uuid.UUID, kind: str, original_filename: str, content_type: str | None,
    data: bytes, actor_id: uuid.UUID, trip_id: uuid.UUID | None = None, defect_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, fueling_id: uuid.UUID | None = None, note: str | None = None,
    commit: bool = True,
) -> Attachment:
    """Every image upload in the app goes through here - vehicle gallery,
    odometer shots, fuel receipts, defect photos, service invoices. One
    pipeline, one validation path, one set of on-disk conventions.

    `commit=False` lets a caller attach a photo inside a larger transaction
    (starting a trip writes the trip and its odometer photo together, so a
    failure cannot leave one without the other)."""
    ext, thumbnail_bytes, full_bytes = process_upload(original_filename, data)

    photos_dir = Path(get_settings().photos_dir)
    thumbnail_filename, full_filename = save_photo_files(photos_dir, ext, thumbnail_bytes, full_bytes)

    attachment = Attachment(
        vehicle_id=vehicle_id, kind=kind, trip_id=trip_id, defect_id=defect_id, service_id=service_id,
        fueling_id=fueling_id, original_filename=original_filename, thumbnail_path=thumbnail_filename,
        full_path=full_filename, mime_type=content_type or f"image/{ext.lstrip('.')}", note=note,
        uploaded_by=actor_id,
    )
    db.add(attachment)
    await db.flush()
    await log_action(
        db, user_id=actor_id, action="create", module=MODULE, entity_type="attachment", entity_id=str(attachment.id),
        after_data={"vehicle_id": str(vehicle_id), "kind": kind, "original_filename": original_filename},
    )
    if commit:
        await db.commit()
        await db.refresh(attachment)
    return attachment


async def link_attachment_to_trip(
    db: AsyncSession, *, attachment_id: uuid.UUID, vehicle_id: uuid.UUID, trip_id: uuid.UUID, commit: bool = False,
) -> None:
    """Přiřadí už nahranou fotografii ke vzniklé jízdě.

    Potřeba kvůli potvrzování OCR: fotka se nahraje při prvním odeslání
    formuláře (jinak by se při druhém kroku ztratila - file input se
    předvyplnit nedá), ale jízda v tu chvíli ještě neexistuje. Kontrola
    `vehicle_id` je tu proto, aby se cizí příloha nedala takhle
    přivlastnit podvrženým id ve skrytém poli (IDOR)."""
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None or attachment.vehicle_id != vehicle_id or attachment.deleted_at is not None:
        raise ValueError("Fotografie k tomuto vozidlu nebyla nalezena.")
    attachment.trip_id = trip_id
    await db.flush()
    if commit:
        await db.commit()


async def link_attachment_to_fueling(
    db: AsyncSession, *, attachment_id: uuid.UUID, vehicle_id: uuid.UUID, trip_id: uuid.UUID,
    fueling_id: uuid.UUID, commit: bool = False,
) -> None:
    """Přiřadí už nahranou účtenku ke vzniklému tankování.

    Potřeba ze stejného důvodu jako link_attachment_to_trip: při
    potvrzování OCR se účtenka nahraje dřív, než tankování existuje.
    Kontrola vozidla brání přivlastnění cizí přílohy podvrženým id."""
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None or attachment.vehicle_id != vehicle_id or attachment.deleted_at is not None:
        raise ValueError("Účtenka k tomuto vozidlu nebyla nalezena.")
    attachment.trip_id = trip_id
    attachment.fueling_id = fueling_id
    await db.flush()
    if commit:
        await db.commit()


async def delete_attachment(db: AsyncSession, attachment: Attachment, actor_id: uuid.UUID) -> None:
    """Soft delete - the row (and the file) stay, so a photo that was
    evidence for a closed trip can never silently vanish from its history."""
    attachment.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    await log_action(
        db, user_id=actor_id, action="delete", module=MODULE, entity_type="attachment",
        entity_id=str(attachment.id),
    )
    await db.commit()
