"""Servisní historie vozidla (zadání 17, Etapa 6).

Záznamy se nikdy nemažou natvrdo - `deleted_at` je soft delete, protože
servisní historie je to, podle čeho se posuzuje stav vozidla i jeho
hodnota.

Výměna oleje má navíc vazbu na stav vozidla: když se zapíše úkon typu
`vymena_oleje`, nabídne se rovnou aktualizace `last_oil_change_at/km` na
vozidle. Bez toho by se servisní kniha a semafor na kartě vozidla
rozešly - servis by byl zapsaný a semafor by dál svítil oranžově.
"""
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import odometer
from app.core.audit import log_action
from app.core.photos import ALLOWED_PHOTO_EXTENSIONS
from app.models.core import User
from app.models.fleet import SERVICE_TYPES, Vehicle, VehicleService
from app.modules.documents import service as documents_service
from app.modules.services import repository
from app.modules.vehicles import service as vehicles_service

MODULE = "services"

# Nad tolik km od aktuálního stavu je zadaný nájezd skoro jistě překlep
# (zadání 32: „servisní km mimo logický rozsah -> upozornění").
ODOMETER_TOLERANCE_KM = 50_000


async def _store_invoice(
    db: AsyncSession, *, vehicle: Vehicle, record: VehicleService, actor: User,
    upload: tuple[str, str | None, bytes], commit: bool,
) -> None:
    """Přiloží k servisnímu úkonu fotografii **nebo** dokument.

    Faktura ze servisu přijde jednou vyfocená mobilem a podruhé e-mailem
    jako PDF - uživatel nemá řešit, kterým tlačítkem ji nahrát, takže
    rozhoduje přípona:

    - obrázek, který umí obrázková pipeline (zmenšování, náhledy), jde
      jako `Attachment`,
    - všechno ostatní (PDF, ale i HEIC, které Pillow neotevře) jde jako
      `VehicleDocument` se zaplněným `service_id` - tedy do úložiště,
      které soubor bere tak, jak je.

    Obojí končí u záznamu, ne v seznamu papírů vozidla (R29). Tohle je
    jediné místo, kde se ta volba dělá - router ani šablona o ní neví."""
    filename, content_type, data = upload
    if Path(filename).suffix.lower() in ALLOWED_PHOTO_EXTENSIONS:
        await vehicles_service.add_attachment(
            db, vehicle_id=vehicle.id, kind="service_invoice", original_filename=filename,
            content_type=content_type, data=data, actor_id=actor.id, service_id=record.id,
            commit=commit,
        )
        return

    await documents_service.add_document(
        db, vehicle=vehicle, actor=actor, doc_type="faktura",
        title=f"Doklad – {filename}", valid_from=None, valid_to=None, note=None,
        filename=filename, content_type=content_type, data=data,
        service_id=record.id, commit=commit,
    )


class ServiceError(Exception):
    """Jednoznačně neplatný vstup."""


class ServiceWarning(Exception):
    """Podezřelé, ale možná správné - projde po potvrzení."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def add_service(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, service_date: date | None,
    service_types: list[str], description: str, odometer_km: int | None, supplier: str | None,
    price_czk: float | None, note: str | None, confirmations: set[str],
    update_oil_interval: bool = False,
    invoice: tuple[str, str | None, bytes] | None = None,
) -> VehicleService:
    if service_date is None:
        raise ServiceError("Datum servisu je povinné.")
    if service_date > date.today():
        raise ServiceError("Datum servisu nemůže být v budoucnosti.")
    service_types = _clean_types(service_types)
    if not description.strip():
        raise ServiceError("Popis úkonu je povinný.")
    if price_czk is not None and price_czk < 0:
        raise ServiceError("Cena nemůže být záporná.")

    _check_odometer(vehicle, odometer_km, confirmations)

    record = VehicleService(
        vehicle_id=vehicle.id,
        service_date=service_date,
        service_types=service_types,
        description=description.strip(),
        odometer_km=odometer_km,
        supplier=(supplier or "").strip() or None,
        price_czk=price_czk,
        note=(note or "").strip() or None,
        created_by=actor.id,
    )
    db.add(record)
    await db.flush()

    if invoice is not None:
        await _store_invoice(db, vehicle=vehicle, record=record, actor=actor, upload=invoice, commit=False)

    # Servis posouvá stav tachometru vozidla. U vozidel, která nevedou
    # knihu jízd, je servis a tankování jediný zdroj - bez toho by
    # tachometr zamrzl a přestal fungovat semafor prohlídky, který se
    # počítá proti aktuálnímu stavu.
    odometer_moved = odometer.advance(vehicle, odometer_km)

    # Uzavření smyčky mezi servisní knihou a semaforem na kartě vozidla.
    oil_updated = False
    if update_oil_interval and suggests_oil_update(service_types):
        vehicle.last_oil_change_at = service_date
        if odometer_km is not None:
            vehicle.last_oil_change_km = odometer_km
        oil_updated = True

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="service",
        entity_id=str(record.id),
        after_data={
            "vehicle_id": str(vehicle.id), "service_types": service_types,
            "service_date": service_date.isoformat(), "odometer_km": odometer_km,
            "price_czk": float(price_czk) if price_czk else None,
            "oil_interval_updated": oil_updated,
            "odometer_moved_to": vehicle.current_odometer_km if odometer_moved else None,
            "confirmations": sorted(confirmations),
        },
    )
    await db.commit()
    return await repository.get(db, record.id)


async def add_attachment(
    db: AsyncSession, *, record: VehicleService, actor: User, photo: tuple[str, str | None, bytes],
) -> None:
    """Další faktura, doklad nebo fotografie k existujícímu záznamu."""
    await _store_invoice(db, vehicle=record.vehicle, record=record, actor=actor, upload=photo, commit=True)


async def delete_service(db: AsyncSession, *, record: VehicleService, actor: User) -> None:
    """Soft delete - servisní historie je podklad pro posouzení stavu i
    hodnoty vozidla, takže řádek zůstává."""
    record.deleted_at = datetime.now(timezone.utc)
    await log_action(
        db, user_id=actor.id, action="delete", module=MODULE, entity_type="service",
        entity_id=str(record.id),
        before_data={"service_types": list(record.service_types),
                     "service_date": record.service_date.isoformat()},
    )
    await db.commit()


def _check_odometer(vehicle: Vehicle, odometer_km: int | None, confirmations: set[str]) -> None:
    if odometer_km is None:
        return
    if odometer_km < 0:
        raise ServiceError("Stav tachometru nemůže být záporný.")
    # Servis může být i zpětně dopsaný, takže nižší hodnota je legitimní.
    # Podezřelý je až velký rozdíl v kterémkoliv směru.
    difference = abs(odometer_km - vehicle.current_odometer_km)
    if difference > ODOMETER_TOLERANCE_KM and "odometer_range" not in confirmations:
        raise ServiceWarning(
            "odometer_range",
            f"Zadaný stav {odometer_km:,} km se od aktuálního stavu vozidla "
            f"({vehicle.current_odometer_km:,} km) liší o {difference:,} km. "
            "Je to správně?".replace(",", " "),
        )


def suggests_oil_update(service_types: list[str]) -> bool:
    return "vymena_oleje" in service_types


def _clean_types(raw: list[str] | None) -> list[str]:
    """Vybrané typy bez duplicit a vždycky ve stejném pořadí.

    Pořadí se bere ze `SERVICE_TYPES`, ne z toho, jak uživatel klikal -
    jinak by dva stejné úkony vypadaly v seznamu pokaždé jinak."""
    chosen = {value for value in (raw or []) if value in SERVICE_TYPES}
    if not chosen:
        raise ServiceError("Vyberte aspoň jeden typ servisního úkonu.")
    return [value for value in SERVICE_TYPES if value in chosen]
