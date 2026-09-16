"""Rezervace vozidel a servisní bloky (zadání 9).

Překryv dvou aktivních rezervací jednoho vozidla nehlídá tenhle kód, ale
databáze (EXCLUDE constraint, migrace 0001 - viz docs/ROZHODNUTI.md R3).
Kontrola v aplikaci by měla mezi dotazem a zápisem okno, kterým dvě
souběžné rezervace projdou obě. Tady se proto porušení constraintu jen
překládá do srozumitelné hlášky.
"""
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.models.core import User
from app.models.fleet import Vehicle, VehicleReservation
from app.modules.notifications import service as notifications
from app.modules.reservations import repository

MODULE = "reservations"

OVERLAP_CONSTRAINT = "ex_fleet_reservations_no_overlap"


class ReservationError(Exception):
    pass


async def create_reservation(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, start_at: datetime, end_at: datetime,
    purpose: str | None, note: str | None, expected_route: str | None, expected_km: int | None,
    kind: str = "reservation", for_user: User | None = None,
) -> VehicleReservation:
    _validate_window(start_at, end_at)
    if kind not in ("reservation", "service"):
        raise ReservationError("Neplatný typ záznamu v kalendáři.")
    if kind == "reservation" and not vehicle.is_active:
        raise ReservationError("Neaktivní vozidlo nelze rezervovat.")
    if expected_km is not None and expected_km < 0:
        raise ReservationError("Očekávaný počet km nemůže být záporný.")

    # Servisní blok patří vozidlu, ne osobě - proto u něj user_id zůstává
    # prázdné (zadání 9: kalendář rozlišuje rezervaci a servis).
    owner = None if kind == "service" else (for_user or actor)

    reservation = VehicleReservation(
        vehicle_id=vehicle.id,
        user_id=owner.id if owner else None,
        kind=kind,
        status="active",
        start_at=start_at,
        end_at=end_at,
        purpose=(purpose or "").strip() or None,
        note=(note or "").strip() or None,
        expected_route=(expected_route or "").strip() or None,
        expected_km=expected_km,
        created_by=actor.id,
    )
    # Savepoint, ne prosté db.rollback(): rollback celé transakce expiruje
    # každý objekt v session, takže by se první následné `user.full_name`
    # při vykreslení formuláře pokusilo dotáhnout data ze session
    # synchronně - a spadlo na MissingGreenlet. Savepoint zahodí jen
    # neúspěšný INSERT a session nechá použitelnou.
    try:
        async with db.begin_nested():
            db.add(reservation)
            await db.flush()
    except IntegrityError as exc:
        raise _overlap_error(exc) from exc

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="reservation",
        entity_id=str(reservation.id),
        after_data={
            "vehicle_id": str(vehicle.id), "kind": kind,
            "start_at": start_at.isoformat(), "end_at": end_at.isoformat(),
            "user_id": str(owner.id) if owner else None,
        },
    )
    await db.commit()

    saved = await repository.get(db, reservation.id)
    if kind == "reservation":
        await notifications.notify_reservation_created(db, vehicle=vehicle, reservation=saved, actor=actor)
    return saved


async def update_reservation(
    db: AsyncSession, *, reservation: VehicleReservation, actor: User, start_at: datetime, end_at: datetime,
    purpose: str | None, note: str | None, expected_route: str | None, expected_km: int | None,
) -> VehicleReservation:
    if reservation.status != "active":
        raise ReservationError("Měnit lze jen aktivní rezervaci.")
    _validate_window(start_at, end_at)

    before = {
        "start_at": reservation.start_at.isoformat(), "end_at": reservation.end_at.isoformat(),
        "purpose": reservation.purpose,
    }
    reservation.start_at = start_at
    reservation.end_at = end_at
    reservation.purpose = (purpose or "").strip() or None
    reservation.note = (note or "").strip() or None
    reservation.expected_route = (expected_route or "").strip() or None
    reservation.expected_km = expected_km

    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        # Savepoint vrátil databázi, ale objekt v paměti si nové hodnoty
        # drží dál - bez expire by je autoflush při vykreslení formuláře
        # zkusil zapsat znovu a spadl na tomtéž.
        db.expire(reservation)
        raise _overlap_error(exc) from exc

    await log_action(
        db, user_id=actor.id, action="update", module=MODULE, entity_type="reservation",
        entity_id=str(reservation.id), before_data=before,
        after_data={"start_at": start_at.isoformat(), "end_at": end_at.isoformat(), "purpose": reservation.purpose},
    )
    await db.commit()
    return await repository.get(db, reservation.id)


async def cancel_reservation(
    db: AsyncSession, *, reservation: VehicleReservation, actor: User, reason: str | None = None,
) -> VehicleReservation:
    """Zrušení termín okamžitě uvolní - EXCLUDE constraint platí jen na
    řádky se status='active'. Řádek zůstává kvůli historii."""
    if reservation.status != "active":
        raise ReservationError("Tato rezervace už není aktivní.")

    reservation.status = "cancelled"
    if reason and reason.strip():
        existing = f"{reservation.note}\n" if reservation.note else ""
        reservation.note = f"{existing}Zrušeno: {reason.strip()}"
    await db.flush()
    await log_action(
        db, user_id=actor.id, action="cancel", module=MODULE, entity_type="reservation",
        entity_id=str(reservation.id), after_data={"reason": (reason or "").strip() or None},
    )
    await db.commit()
    return await repository.get(db, reservation.id)


async def mark_fulfilled(db: AsyncSession, reservation: VehicleReservation) -> None:
    """Rezervace, na kterou její majitel skutečně vyjel. Volá se z
    trips/service.py při zahájení jízdy; commit dělá volající, aby jízda
    a její rezervace skončily v jedné transakci."""
    reservation.status = "fulfilled"
    await db.flush()


def _validate_window(start_at: datetime, end_at: datetime) -> None:
    if start_at is None or end_at is None:
        raise ReservationError("Vyplňte začátek i konec rezervace.")
    if end_at <= start_at:
        raise ReservationError("Konec rezervace musí být později než její začátek.")


def _overlap_error(exc: IntegrityError) -> ReservationError:
    """EXCLUDE constraint je jediná spolehlivá ochrana proti překryvu,
    takže se na jeho porušení musí umět odpovědět srozumitelně - ne
    pádem na 500."""
    if OVERLAP_CONSTRAINT in str(exc.orig):
        return ReservationError(
            "V tomto termínu už je vozidlo rezervované nebo odstavené. Vyberte jiný čas."
        )
    return ReservationError("Rezervaci se nepodařilo uložit.")


def can_manage_reservation(reservation: VehicleReservation, actor: User, codes: set[str]) -> bool:
    """Svoji rezervaci spravuje každý; cizí jen ten, kdo na to má
    oprávnění. Servisní blok nemá majitele, takže patří vždy správci."""
    if "fleet.reservation.manage" in codes or "fleet.vehicle.manage" in codes:
        return True
    return reservation.kind == "reservation" and reservation.user_id == actor.id


def describe(reservation: VehicleReservation) -> str:
    """Jedna věta do varování při výpůjčce (zadání 10)."""
    who = reservation.user.full_name if reservation.user else "servis / mimo provoz"
    return (
        f"{who}, od {reservation.start_at.strftime('%d.%m.%Y %H:%M')} "
        f"do {reservation.end_at.strftime('%d.%m.%Y %H:%M')}"
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


