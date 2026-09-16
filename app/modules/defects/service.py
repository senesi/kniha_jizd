"""Závady vozidel (zadání 16, Etapa 4).

Závada se hlásí dvěma cestami - z probíhající jízdy (typicky „něco se
stalo cestou") nebo přímo z karty vozidla. Obě končí stejným záznamem;
jen ta z jízdy si navíc pamatuje, ve které jízdě vznikla.

Řádky se nikdy nemažou: vyřešená závada zůstává v historii vozidla
(zadání 16). Změny stavu se auditují, aby bylo dohledatelné, kdo a kdy
závadu převzal a uzavřel.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import MANAGE_ANY, MANAGE_OWN
from app.core.audit import log_action
from app.models.core import User
from app.models.fleet import DEFECT_PRIORITIES, DEFECT_STATUSES, Vehicle, VehicleDefect
from app.modules.defects import repository
from app.modules.notifications import service as notifications
from app.modules.vehicles import service as vehicles_service

MODULE = "defects"


class DefectError(Exception):
    pass


def can_manage_defect(vehicle: Vehicle, actor: User, codes: set[str]) -> bool:
    """Řešit závady smí administrátor, držitel fleet.defect.manage a
    odpovědná osoba daného vozidla."""
    if MANAGE_ANY in codes or "fleet.defect.manage" in codes:
        return True
    return MANAGE_OWN in codes and vehicle.responsible_user_id == actor.id


async def report_defect(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, description: str, priority: str,
    trip_id: uuid.UUID | None = None, photo: tuple[str, str | None, bytes] | None = None,
) -> VehicleDefect:
    if not description.strip():
        raise DefectError("Popis závady je povinný.")
    if priority not in DEFECT_PRIORITIES:
        raise DefectError("Vyberte prioritu závady.")

    defect = VehicleDefect(
        vehicle_id=vehicle.id,
        trip_id=trip_id,
        reported_by=actor.id,
        description=description.strip(),
        priority=priority,
        status="new",
    )
    db.add(defect)
    await db.flush()

    # Fotka ve stejné transakci jako závada - aby nemohla vzniknout
    # závada bez svého důkazu ani naopak.
    if photo is not None:
        filename, content_type, data = photo
        await vehicles_service.add_attachment(
            db, vehicle_id=vehicle.id, kind="defect_photo", original_filename=filename,
            content_type=content_type, data=data, actor_id=actor.id, defect_id=defect.id,
            trip_id=trip_id, commit=False,
        )

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="defect", entity_id=str(defect.id),
        after_data={
            "vehicle_id": str(vehicle.id), "priority": priority, "status": "new",
            "trip_id": str(trip_id) if trip_id else None,
        },
    )
    await db.commit()

    saved = await repository.get(db, defect.id)
    await notifications.notify_defect_reported(db, vehicle=vehicle, defect=saved, actor=actor)
    return saved


async def add_photo(
    db: AsyncSession, *, defect: VehicleDefect, actor: User, photo: tuple[str, str | None, bytes],
) -> None:
    filename, content_type, data = photo
    await vehicles_service.add_attachment(
        db, vehicle_id=defect.vehicle_id, kind="defect_photo", original_filename=filename,
        content_type=content_type, data=data, actor_id=actor.id, defect_id=defect.id,
    )


async def change_status(
    db: AsyncSession, *, defect: VehicleDefect, actor: User, new_status: str, note: str | None,
) -> VehicleDefect:
    """Přechod mezi stavy new -> in_progress -> resolved (a zpět, když se
    ukáže, že vyřešeno nebylo)."""
    if new_status not in DEFECT_STATUSES:
        raise DefectError("Neplatný stav závady.")
    if new_status == defect.status:
        raise DefectError("Závada už v tomto stavu je.")
    if new_status == "resolved" and not (note or "").strip():
        raise DefectError("U vyřešené závady vyplňte, jak byla vyřešena.")

    before = {"status": defect.status, "resolution_note": defect.resolution_note}
    defect.status = new_status
    if (note or "").strip():
        defect.resolution_note = note.strip()

    if new_status == "resolved":
        defect.resolved_at = datetime.now(timezone.utc)
        defect.resolved_by = actor.id
    else:
        # Znovuotevření musí smazat i stopu po „vyřešení", jinak by karta
        # vozidla tvrdila, že závada je vyřešená, a zároveň že se řeší.
        defect.resolved_at = None
        defect.resolved_by = None

    await db.flush()
    await log_action(
        db, user_id=actor.id, action="status_change", module=MODULE, entity_type="defect",
        entity_id=str(defect.id), before_data=before,
        after_data={"status": new_status, "resolution_note": defect.resolution_note},
    )
    await db.commit()

    saved = await repository.get(db, defect.id)
    await notifications.notify_defect_status_changed(db, defect=saved, actor=actor)
    return saved


async def change_priority(
    db: AsyncSession, *, defect: VehicleDefect, actor: User, priority: str,
) -> VehicleDefect:
    if priority not in DEFECT_PRIORITIES:
        raise DefectError("Neplatná priorita.")
    if priority == defect.priority:
        return defect

    before = {"priority": defect.priority}
    defect.priority = priority
    await db.flush()
    await log_action(
        db, user_id=actor.id, action="priority_change", module=MODULE, entity_type="defect",
        entity_id=str(defect.id), before_data=before, after_data={"priority": priority},
    )
    await db.commit()
    return await repository.get(db, defect.id)


def critical_warning_text(defects: list[VehicleDefect]) -> str | None:
    """Text výrazného upozornění při pokusu o výpůjčku (zadání 16).

    Kritická závada jízdu NEBLOKUJE - zadání to výslovně nežádá a auto s
    prasklým zrcátkem musí jít odvézt do servisu. Ale řidič ji musí vidět
    a potvrdit, že o ní ví."""
    critical = [defect for defect in defects if defect.priority == "critical" and defect.is_open]
    if not critical:
        return None
    if len(critical) == 1:
        return f"Vozidlo má nevyřešenou KRITICKOU závadu: {critical[0].description}"
    joined = "; ".join(defect.description for defect in critical[:3])
    return f"Vozidlo má {len(critical)} nevyřešené kritické závady: {joined}"
