"""Creating and delivering notifications (zadání 20).

Every notify_* helper follows the same two steps: write the row (so it is
visible in-app and in history no matter what), then attempt the e-mail and
record the outcome on that row. The mail attempt can never fail the
caller's workflow - see app/core/mailer.py.

The recipient is the vehicle's responsible person. If a vehicle has none,
or the responsible person is the very actor who caused the event, nothing
is sent: telling someone about their own action is noise, not a
notification.
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mailer
from app.models.core import User
from app.models.fleet import Notification, Vehicle

logger = logging.getLogger(__name__)

BASE_PATH = "/kniha-jizd"


async def _recipient(db: AsyncSession, vehicle: Vehicle, actor_id: uuid.UUID | None) -> User | None:
    if vehicle.responsible_user_id is None or vehicle.responsible_user_id == actor_id:
        return None
    result = await db.execute(
        select(User).where(User.id == vehicle.responsible_user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession, *, user: User, vehicle: Vehicle | None, kind: str, title: str, body: str,
    link_url: str | None = None, dedupe_key: str | None = None, commit: bool = True,
) -> Notification:
    notification = Notification(
        user_id=user.id, vehicle_id=vehicle.id if vehicle else None, kind=kind, title=title, body=body,
        link_url=link_url, dedupe_key=dedupe_key,
    )
    db.add(notification)
    await db.flush()

    error = await mailer.send_mail(user.email, title, _mail_body(body, link_url))
    if error is None:
        notification.emailed_at = datetime.now(timezone.utc)
    else:
        notification.email_error = error
    await db.flush()
    if commit:
        await db.commit()
    return notification


def _mail_body(body: str, link_url: str | None) -> str:
    if not link_url:
        return body
    return f"{body}\n\nOtevřít v aplikaci: https://solareg.azunimb.cz{link_url}\n"


async def _notify_responsible(
    db: AsyncSession, *, vehicle: Vehicle, actor_id: uuid.UUID | None, kind: str, title: str, body: str,
    link_url: str | None = None, commit: bool = True,
) -> Notification | None:
    recipient = await _recipient(db, vehicle, actor_id)
    if recipient is None:
        return None
    return await create(
        db, user=recipient, vehicle=vehicle, kind=kind, title=title, body=body, link_url=link_url, commit=commit,
    )


def _vehicle_label(vehicle: Vehicle) -> str:
    return f"{vehicle.internal_code} ({vehicle.license_plate})"


async def notify_reservation_created(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor: User, commit: bool = True
):
    who = reservation.user.full_name if reservation.user else actor.full_name
    return await _notify_responsible(
        db, vehicle=vehicle, actor_id=actor.id, kind="reservation",
        title=f"Nová rezervace – {_vehicle_label(vehicle)}",
        body=(
            f"{who} rezervoval(a) vozidlo {_vehicle_label(vehicle)}\n"
            f"Od: {reservation.start_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Do: {reservation.end_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Účel: {reservation.purpose or '-'}"
        ),
        link_url=f"{BASE_PATH}/reservations/{reservation.id}",
        commit=commit,
    )


async def notify_trip_started(db: AsyncSession, *, vehicle: Vehicle, trip, actor: User, commit: bool = True):
    return await _notify_responsible(
        db, vehicle=vehicle, actor_id=actor.id, kind="trip_start",
        title=f"Zahájena výpůjčka – {_vehicle_label(vehicle)}",
        body=(
            f"{actor.full_name} zahájil(a) výpůjčku vozidla {_vehicle_label(vehicle)}\n"
            f"Čas: {trip.started_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Stav km: {trip.start_odometer_km}"
        ),
        link_url=f"{BASE_PATH}/trips/{trip.id}",
        commit=commit,
    )


async def notify_trip_ended(db: AsyncSession, *, vehicle: Vehicle, trip, actor: User, commit: bool = True):
    return await _notify_responsible(
        db, vehicle=vehicle, actor_id=actor.id, kind="trip_end",
        title=f"Ukončena výpůjčka – {_vehicle_label(vehicle)}",
        body=(
            f"{actor.full_name} ukončil(a) výpůjčku vozidla {_vehicle_label(vehicle)}\n"
            f"Čas: {trip.ended_at.strftime('%d.%m.%Y %H:%M') if trip.ended_at else '-'}\n"
            f"Ujeto: {trip.distance_km} km (konečný stav {trip.end_odometer_km} km)\n"
            f"Účel: {trip.purpose_text or trip.purpose_code or '-'}\n"
            f"Trasa: {trip.route_text or '-'}"
        ),
        link_url=f"{BASE_PATH}/trips/{trip.id}",
        commit=commit,
    )


async def notify_defect_reported(db: AsyncSession, *, vehicle: Vehicle, defect, actor: User, commit: bool = True):
    priority_labels = {"low": "nízká", "normal": "běžná", "high": "vysoká", "critical": "KRITICKÁ"}
    return await _notify_responsible(
        db, vehicle=vehicle, actor_id=actor.id, kind="defect",
        title=f"Nahlášena závada ({priority_labels.get(defect.priority, defect.priority)}) – {_vehicle_label(vehicle)}",
        body=(
            f"{actor.full_name} nahlásil(a) závadu na vozidle {_vehicle_label(vehicle)}\n"
            f"Priorita: {priority_labels.get(defect.priority, defect.priority)}\n\n"
            f"{defect.description}"
        ),
        link_url=f"{BASE_PATH}/defects/{defect.id}",
        commit=commit,
    )


async def notify_approval_requested(db: AsyncSession, *, vehicle: Vehicle, request, actor: User, commit: bool = True):
    """Žádost o schválení musí dorazit tomu, kdo o ní rozhoduje.

    Odpovědná osoba ani administrátor o vlastní vozidlo nežádají (viz
    approvals/service.py:needs_approval), takže tady nehrozí, že by si
    někdo posílal upozornění sám sobě."""
    period = ""
    if request.needed_from:
        period = f"\nOd: {request.needed_from.strftime('%d.%m.%Y %H:%M')}"
        if request.needed_to:
            period += f"\nDo: {request.needed_to.strftime('%d.%m.%Y %H:%M')}"
    return await _notify_responsible(
        db, vehicle=vehicle, actor_id=actor.id, kind="approval_request",
        title=f"Žádost o použití vozidla – {_vehicle_label(vehicle)}",
        body=(
            f"{actor.full_name} žádá o použití vozidla {_vehicle_label(vehicle)}"
            f"{period}\nÚčel: {request.purpose or '-'}"
        ),
        link_url=f"{BASE_PATH}/approvals/{request.id}",
        commit=commit,
    )


async def notify_approval_decided(db: AsyncSession, *, request, actor: User, commit: bool = True):
    """Výsledek jde žadateli - ten se musí dozvědět, jestli může jet."""
    approved = request.status == "approved"
    result = await db.execute(
        select(User).where(User.id == request.requester_id, User.is_active.is_(True))
    )
    requester = result.scalar_one_or_none()
    if requester is None or requester.id == actor.id:
        return None

    label = _vehicle_label(request.vehicle)
    verb = "schválil(a)" if approved else "zamítl(a)"
    body = f"{actor.full_name} {verb} vaši žádost o vozidlo {label}."
    if approved and request.valid_until:
        body += f"\nVýpůjčku zahajte do {request.valid_until.strftime('%d.%m.%Y %H:%M')}."
    if request.decision_note:
        body += f"\n\n{request.decision_note}"

    return await create(
        db, user=requester, vehicle=request.vehicle, kind="approval_decision",
        title=f"Žádost {'schválena' if approved else 'zamítnuta'} – {label}",
        body=body, link_url=f"{BASE_PATH}/approvals/{request.id}", commit=commit,
    )


DEFECT_STATUS_WORDS = {"new": "nová", "in_progress": "řeší se", "resolved": "vyřešena"}


async def notify_defect_status_changed(db: AsyncSession, *, defect, actor: User, commit: bool = True):
    """Změnu stavu závady hlásíme tomu, kdo ji nahlásil - jeho se týká
    nejvíc, a odpovědná osoba ji obvykle mění sama."""
    result = await db.execute(
        select(User).where(User.id == defect.reported_by, User.is_active.is_(True))
    )
    reporter = result.scalar_one_or_none()
    if reporter is None or reporter.id == actor.id:
        return None

    word = DEFECT_STATUS_WORDS.get(defect.status, defect.status)
    label = _vehicle_label(defect.vehicle)
    body = f"{actor.full_name} změnil(a) stav vaší závady na „{word}“.\n\n{defect.description}"
    if defect.resolution_note:
        body += f"\n\nŘešení: {defect.resolution_note}"

    return await create(
        db, user=reporter, vehicle=defect.vehicle, kind="defect",
        title=f"Závada: {word} – {label}",
        body=body, link_url=f"{BASE_PATH}/defects/{defect.id}", commit=commit,
    )


async def mark_read(db: AsyncSession, notification: Notification) -> None:
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()


async def mark_all_read(db: AsyncSession, user_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    now = datetime.now(timezone.utc)
    for notification in result.scalars().all():
        notification.read_at = now
    await db.commit()
