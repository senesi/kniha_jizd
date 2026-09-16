"""Žádosti o použití vozidla se schvalováním (požadavek B).

Vozidlo s `approval_required` si řidič nemůže prostě vzít - musí nejdřív
dostat souhlas odpovědné osoby nebo administrátora. Vozidla bez toho
příznaku fungují úplně stejně jako dosud; tenhle modul se jich nijak
nedotkne.

Schválení NENÍ jízda. Že se schválení použilo, se pozná z
`Trip.request_id` (unikátní sloupec), ne ze stavu na žádosti - a právě
ta unikátnost brání dvěma souběžným výjezdům na jedno schválení. Stejný
princip jako u rezervací v Etapě 3.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.access import MANAGE_ANY, MANAGE_OWN
from app.models.core import User
from app.models.fleet import TripRequest, Vehicle
from app.modules.approvals import repository
from app.modules.notifications import service as notifications

MODULE = "approvals"

# Jak dlouho platí schválení, než se musí proměnit v jízdu. Schvalovatel
# nepočítá s tím, že si někdo vozidlo vezme za tři týdny - a nepoužité
# schválení by jinak viselo navždy.
APPROVAL_VALID_HOURS = 72

PENDING_CONSTRAINT = "uq_fleet_trip_requests_one_pending"


class ApprovalError(Exception):
    pass


def needs_approval(vehicle: Vehicle, actor: User, codes: set[str]) -> bool:
    """Kdo o vozidle rozhoduje, ten o něj nežádá. Odpovědná osoba a
    administrátor si vozidlo berou přímo - žádost sami sobě by byla jen
    obřad navíc."""
    if not vehicle.approval_required:
        return False
    return not can_decide(vehicle, actor, codes)


def can_decide(vehicle: Vehicle, actor: User, codes: set[str]) -> bool:
    if MANAGE_ANY in codes or "fleet.trip.manage" in codes:
        return True
    return MANAGE_OWN in codes and vehicle.responsible_user_id == actor.id


async def create_request(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, purpose: str | None,
    needed_from: datetime | None, needed_to: datetime | None,
) -> TripRequest:
    if not vehicle.approval_required:
        raise ApprovalError("Toto vozidlo schválení nevyžaduje – můžete rovnou zahájit výpůjčku.")
    if not vehicle.is_active:
        raise ApprovalError("Vozidlo je neaktivní.")
    if needed_from and needed_to and needed_to <= needed_from:
        raise ApprovalError("Konec požadovaného období musí být později než jeho začátek.")

    existing = await repository.get_pending_for(db, vehicle_id=vehicle.id, requester_id=actor.id)
    if existing is not None:
        raise ApprovalError("Na tohle vozidlo už máte čekající žádost.")

    usable = await repository.get_usable_approval(db, vehicle_id=vehicle.id, requester_id=actor.id)
    if usable is not None:
        raise ApprovalError("Na tohle vozidlo už máte platné schválení – můžete zahájit výpůjčku.")

    request = TripRequest(
        vehicle_id=vehicle.id, requester_id=actor.id, status="pending",
        purpose=(purpose or "").strip() or None, needed_from=needed_from, needed_to=needed_to,
    )
    try:
        # Savepoint, ne rollback celé transakce - ta by expirovala objekty
        # v session a následné vykreslení stránky by spadlo (viz
        # docs/ROZHODNUTI.md R15).
        async with db.begin_nested():
            db.add(request)
            await db.flush()
    except IntegrityError as exc:
        if PENDING_CONSTRAINT in str(exc.orig):
            raise ApprovalError("Na tohle vozidlo už máte čekající žádost.") from exc
        raise ApprovalError("Žádost se nepodařilo uložit.") from exc

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="trip_request",
        entity_id=str(request.id),
        after_data={"vehicle_id": str(vehicle.id), "purpose": request.purpose},
    )
    await db.commit()

    saved = await repository.get(db, request.id)
    await notifications.notify_approval_requested(db, vehicle=vehicle, request=saved, actor=actor)
    return saved


async def decide(
    db: AsyncSession, *, request: TripRequest, actor: User, approve: bool, note: str | None,
) -> TripRequest:
    if request.status != "pending":
        raise ApprovalError("O této žádosti už bylo rozhodnuto.")
    if not approve and not (note or "").strip():
        raise ApprovalError("U zamítnutí vyplňte důvod – žadatel se musí dozvědět proč.")

    now = datetime.now(timezone.utc)
    request.status = "approved" if approve else "rejected"
    request.decided_at = now
    request.decided_by = actor.id
    request.decision_note = (note or "").strip() or None
    # valid_until se nastavuje AŽ tady: čekající žádost nikdy nevyprší
    # sama od sebe, ale schválení má omezenou platnost.
    request.valid_until = now + timedelta(hours=APPROVAL_VALID_HOURS) if approve else None
    await db.flush()

    await log_action(
        db, user_id=actor.id, action="approve" if approve else "reject", module=MODULE,
        entity_type="trip_request", entity_id=str(request.id),
        after_data={"note": request.decision_note, "valid_until": request.valid_until.isoformat() if approve else None},
    )
    await db.commit()

    saved = await repository.get(db, request.id)
    await notifications.notify_approval_decided(db, request=saved, actor=actor)
    return saved


async def cancel_request(db: AsyncSession, *, request: TripRequest, actor: User) -> TripRequest:
    if request.status != "pending":
        raise ApprovalError("Stáhnout lze jen čekající žádost.")
    request.status = "cancelled"
    request.decided_at = datetime.now(timezone.utc)
    await db.flush()
    await log_action(
        db, user_id=actor.id, action="cancel", module=MODULE, entity_type="trip_request",
        entity_id=str(request.id),
    )
    await db.commit()
    return await repository.get(db, request.id)


async def consume_for_trip(db: AsyncSession, *, request: TripRequest, trip_id: uuid.UUID) -> None:
    """Zaznamená, že se schválení proměnilo v jízdu. Volá se z
    trips/service.py uvnitř téže transakce; `Trip.request_id` je unikátní,
    takže druhý souběžný pokus o výjezd na totéž schválení neprojde."""
    await log_action(
        db, user_id=request.requester_id, action="consume", module=MODULE,
        entity_type="trip_request", entity_id=str(request.id),
        after_data={"trip_id": str(trip_id)},
    )


def can_view_request(request: TripRequest, actor: User, codes: set[str]) -> bool:
    if request.requester_id == actor.id:
        return True
    return can_decide(request.vehicle, actor, codes)
