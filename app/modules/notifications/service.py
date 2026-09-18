"""Creating and delivering notifications (zadání 20).

Every notify_* helper follows the same two steps: write the row (so it is
visible in-app and in history no matter what), then attempt the e-mail and
record the outcome on that row. The mail attempt can never fail the
caller's workflow - see app/core/mailer.py.

The recipient is the vehicle's responsible person. If a vehicle has none,
or the responsible person is the very actor who caused the event, nothing
is sent: telling someone about their own action is noise, not a
notification.

O tom, jestli se zpráva **skutečně odešle**, rozhoduje individuální
nastavení příjemce (`preferences.is_enabled`). Kontrola je v `create()`,
tedy v jediném místě, kterým prochází každá notifikace - aby nešlo
přidat nový notify_* helper a na preference u něj zapomenout. Rozhoduje
se za každého příjemce zvlášť: dva lidé u téže události můžou mít různé
nastavení a jeden zprávu dostane, druhý ne.
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications import repository

from app.core import app_settings, mailer
from app.models.core import User
from app.models.fleet import Notification, Vehicle
from app.modules.notifications import preferences

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
) -> Notification | None:
    """Zapíše notifikaci a pokusí se ji odeslat e-mailem.

    Vrací `None`, když ji příjemce nechce (vypnutý typ v jeho nastavení)
    nebo když už tutéž zprávu dostal (`dedupe_key`). Volající to nemusí
    řešit - žádný z nich návratovou hodnotu nepotřebuje."""
    if not await preferences.is_enabled(db, user.id, kind=kind):
        # Vypnuté = nevznikne ani řádek. Zapisovat do schránky něco, co
        # si člověk vypnul, by z "vypnuto" udělalo "jen bez e-mailu".
        return None

    notification = None
    if dedupe_key is not None:
        notification, busy = await repository.claim_for_delivery(db, user.id, dedupe_key)
        if busy:
            # Tutéž zprávu právě vyřizuje jiný souběžný běh.
            return None
        if notification is not None and notification.emailed_at is not None:
            # Doručeno dřív - tohle je ta jediná situace, kdy se
            # neopakuje. Neúspěšný pokus se zkusit smí (R43).
            return None

    if notification is None:
        notification = Notification(
            user_id=user.id, vehicle_id=vehicle.id if vehicle else None, kind=kind, title=title,
            body=body, link_url=link_url, dedupe_key=dedupe_key,
        )
        try:
            # Savepoint, ne prostý insert: při souběhu zakládání prohraje
            # jeden z běhů na unikátním indexu a nesmí tím shodit celou
            # transakci ani session (stejný důvod jako R15).
            #
            # `db.add` patří DOVNITŘ savepointu. Kdyby byl venku, zůstal
            # by objekt po rollbacku mezi rozepsanými a příští autoflush
            # by tentýž INSERT zkusil znovu - session by se tím otrávila.
            async with db.begin_nested():
                db.add(notification)
                await db.flush()
        except IntegrityError:
            # Souběžný běh nás předběhl; práci dokončí on.
            return None

    # Odeslání až teď a jeho výsledek rozhoduje o tom, jestli je hotovo.
    smtp = await app_settings.get_smtp(db)
    notification.email_attempts = (notification.email_attempts or 0) + 1
    error = await mailer.send_mail(smtp, user.email, title, _mail_body(body, link_url))
    if error is None:
        notification.emailed_at = datetime.now(timezone.utc)
        notification.email_error = None
    else:
        notification.email_error = error
        logger.warning(
            "Notifikace %s pro %s se neodeslala (pokus %s): %s",
            notification.id, user.email, notification.email_attempts, error,
        )
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


async def _reservation_recipients(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor_id: uuid.UUID | None,
) -> list[User]:
    """Majitel rezervace a odpovědná osoba vozidla, každý nejvýš jednou.

    Majitel je `reservation.user_id`, ne ten, kdo klikl - kdyby rezervaci
    zakládal správce za někoho jiného, dozvědět se to má ten, komu
    vozidlo pojede.

    **Zakladatel dostane potvrzení i o vlastní rezervaci.** U ostatních
    typů notifikací platí, že o vlastní akci se člověku nepíše, ale tady
    to zadání chce výslovně a dává to smysl: potvrzení rezervace je
    doklad, že termín skutečně vznikl. Komu to vadí, vypne si přepínač
    „Rezervace vozidel" - od toho tam je.

    `actor_id` se tedy nepoužívá k vyřazení příjemce; zůstává v podpisu,
    protože rozhodnutí patří sem, ne do volajícího.

    Jeden člověk nedostane dvě zprávy o téže věci, i když je současně
    majitelem rezervace a odpovědnou osobou - `dict.fromkeys` sjednotí
    id dřív, než se cokoliv odešle."""
    wanted = [
        candidate for candidate in dict.fromkeys(
            [reservation.user_id, vehicle.responsible_user_id]
        )
        if candidate is not None
    ]
    if not wanted:
        return []
    result = await db.execute(
        select(User).where(User.id.in_(wanted), User.is_active.is_(True))
    )
    return list(result.scalars().all())


def _reservation_period(reservation) -> str:
    return (
        f"Od: {reservation.start_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"Do: {reservation.end_at.strftime('%d.%m.%Y %H:%M')}"
    )


async def _notify_reservation(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor: User, kind: str,
    title: str, body: str, commit: bool = True,
) -> list[Notification]:
    sent = []
    for recipient in await _reservation_recipients(
        db, vehicle=vehicle, reservation=reservation, actor_id=actor.id,
    ):
        notification = await create(
            db, user=recipient, vehicle=vehicle, kind=kind, title=title, body=body,
            link_url=f"{BASE_PATH}/reservations/{reservation.id}", commit=False,
        )
        if notification is not None:
            sent.append(notification)
    if commit:
        await db.commit()
    return sent


async def notify_reservation_created(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor: User, commit: bool = True
):
    who = reservation.user.full_name if reservation.user else actor.full_name
    return await _notify_reservation(
        db, vehicle=vehicle, reservation=reservation, actor=actor, kind="reservation",
        title=f"Nová rezervace – {_vehicle_label(vehicle)}",
        body=(
            f"{who} rezervoval(a) vozidlo {_vehicle_label(vehicle)}\n"
            f"{_reservation_period(reservation)}\n"
            f"Účel: {reservation.purpose or '-'}"
        ),
        commit=commit,
    )


async def notify_reservation_changed(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor: User, commit: bool = True
):
    return await _notify_reservation(
        db, vehicle=vehicle, reservation=reservation, actor=actor, kind="reservation_change",
        title=f"Změna rezervace – {_vehicle_label(vehicle)}",
        body=(
            f"{actor.full_name} změnil(a) rezervaci vozidla {_vehicle_label(vehicle)}\n"
            f"Nový termín:\n{_reservation_period(reservation)}\n"
            f"Účel: {reservation.purpose or '-'}"
        ),
        commit=commit,
    )


async def notify_reservation_cancelled(
    db: AsyncSession, *, vehicle: Vehicle, reservation, actor: User, reason: str | None = None,
    commit: bool = True,
):
    body = (
        f"{actor.full_name} zrušil(a) rezervaci vozidla {_vehicle_label(vehicle)}\n"
        f"{_reservation_period(reservation)}"
    )
    if reason:
        body += f"\n\nDůvod: {reason}"
    return await _notify_reservation(
        db, vehicle=vehicle, reservation=reservation, actor=actor, kind="reservation_change",
        title=f"Zrušená rezervace – {_vehicle_label(vehicle)}",
        body=body, commit=commit,
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


# --- připomínky termínů vozidla (zadání 19/20) -------------------------

async def deadline_recipients(db: AsyncSession, vehicle: Vehicle) -> list[User]:
    """Odpovědná osoba vozidla a administrátoři, každý nejvýš jednou.

    Administrátor se pozná podle oprávnění `fleet.vehicle.manage`, ne
    podle názvu role - role se dají přejmenovat a přidat, oprávnění je
    to, co skutečně znamená „spravuje celý vozový park".

    Tahle funkce vrací **koho se to týká**, ne komu se odešle. Jestli
    zprávu opravdu chtějí, rozhodne individuální nastavení každého z
    nich až v `create()`. Odpovědná osoba tedy nedostává nic automaticky
    jen proto, že je odpovědná osoba."""
    from app.models.core import Permission, Role, RolePermission, UserRole

    admins = (
        select(UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.code == "fleet.vehicle.manage")
    )
    condition = User.id.in_(admins)
    if vehicle.responsible_user_id is not None:
        condition = condition | (User.id == vehicle.responsible_user_id)

    result = await db.execute(
        select(User).where(condition, User.is_active.is_(True)).order_by(User.full_name)
    )
    return list(result.scalars().all())


async def notify_vehicle_deadline(
    db: AsyncSession, *, vehicle: Vehicle, deadline, commit: bool = True,
) -> list[Notification]:
    """Jedna připomínka jednoho termínu (STK, pojištění, známka, servis).

    `dedupe_key` obsahuje kód termínu i jeho datum, takže:

    - připomínka, která **doopravdy dorazila**, se už neposílá znovu,
    - jakmile se termín posune (nová STK), klíč se změní a příští
      připomínka projde.

    Neúspěšné odeslání hotovo není: řádek zůstane s prázdným
    `emailed_at` a zítřejší běh to zkusí znovu (R43).

    Vrací notifikace, se kterými se v tomhle běhu něco dělo - tedy nově
    založené i ty, u kterých se opakovalo odesílání. Jestli e-mail
    opravdu odešel, říká `notification.email_status`."""
    if not deadline.is_actionable:
        return []

    due = deadline.due_date.isoformat() if deadline.due_date else "bez-data"
    dedupe_key = f"deadline:{vehicle.id}:{deadline.code}:{due}:{deadline.level}"

    handled = []
    for recipient in await deadline_recipients(db, vehicle):
        notification = await create(
            db, user=recipient, vehicle=vehicle, kind="deadline",
            title=f"{deadline.label} – {_vehicle_label(vehicle)}",
            body=f"{_vehicle_label(vehicle)}: {deadline.detail}",
            link_url=f"{BASE_PATH}/vehicles/{vehicle.id}",
            dedupe_key=dedupe_key, commit=False,
        )
        if notification is not None:
            handled.append(notification)
    if commit:
        await db.commit()
    return handled
