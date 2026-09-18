"""Doručení rozhoduje o deduplikaci, ne existence řádku (zadání 20).

Dřív stačilo, že řádek s `dedupe_key` vznikl, a připomínka se už nikdy
neposlala. Když v tu chvíli pošta nefungovala, e-mail nedorazil nikdy.

Nově je rozhodující `emailed_at`. Co se nepodařilo odeslat, se zítra
zkusí znovu — a co dorazilo, se neopakuje.
"""
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import create_vehicle


# --- pomůcky -----------------------------------------------------------

@pytest.fixture
def mail(monkeypatch):
    """Řízené odesílání: dá se přepnout na selhávající a zpátky."""
    from app.core import mailer

    state = {"fail": None, "sent": []}

    async def fake_send(config, to_email, subject, body):
        if state["fail"] is not None:
            return state["fail"]
        state["sent"].append((to_email, subject))
        return None

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return state


async def _user_first():
    from app.core.db import async_session_factory
    from app.models.core import User

    async with async_session_factory() as db:
        return (await db.execute(select(User).order_by(User.created_at))).scalars().first()


async def _vehicle_row(vehicle_id: str):
    import uuid as _uuid
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(
            select(Vehicle).where(Vehicle.id == _uuid.UUID(vehicle_id))
        )).scalar_one()


def _deadline():
    from app.core.fleet_status import DeadlineStatus

    return DeadlineStatus(
        code="stk", label="STK", level="red",
        due_date=date.today() + timedelta(days=2), days_left=2,
        detail="STK končí za 2 dny.",
    )


async def _notify(vehicle_id: str):
    """Jeden běh plánovače nad jedním vozidlem."""
    from app.core.db import async_session_factory
    from app.modules.notifications import service as notifications

    vehicle = await _vehicle_row(vehicle_id)
    async with async_session_factory() as db:
        return await notifications.notify_vehicle_deadline(
            db, vehicle=vehicle, deadline=_deadline(),
        )


async def _rows(vehicle_id: str) -> list:
    import uuid as _uuid
    from app.core.db import async_session_factory
    from app.models.fleet import Notification

    async with async_session_factory() as db:
        return list((await db.execute(
            select(Notification)
            .where(Notification.vehicle_id == _uuid.UUID(vehicle_id), Notification.kind == "deadline")
            .order_by(Notification.created_at)
        )).scalars().all())


# --- úspěch se neopakuje ------------------------------------------------

async def test_delivered_reminder_is_not_sent_again(logged_in_client, csrf_token, mail):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-01", license_plate="1DL 0001",
    )

    first = await _notify(vehicle_id)
    assert first, "první běh musí něco poslat"
    assert all(n.email_status == "sent" for n in first)
    delivered = len(mail["sent"])
    assert delivered > 0

    second = await _notify(vehicle_id)
    assert second == [], "doručené se neopakuje"
    assert len(mail["sent"]) == delivered, "žádný e-mail navíc"


# --- neúspěch se zkusí znovu --------------------------------------------

async def test_failed_delivery_is_retried_on_the_next_run(logged_in_client, csrf_token, mail):
    """Tohle je ta chyba, kvůli které se celá logika měnila."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-02", license_plate="1DL 0002",
    )

    mail["fail"] = "Connection refused"
    first = await _notify(vehicle_id)
    assert first, "notifikace vznikne i když pošta selže"
    assert all(n.email_status == "failed" for n in first)
    assert mail["sent"] == [], "nic nedorazilo"

    rows = await _rows(vehicle_id)
    assert all(row.emailed_at is None for row in rows)
    assert all(row.email_error == "Connection refused" for row in rows)
    assert all(row.email_attempts == 1 for row in rows)

    # Pošta se spraví a další běh to zkusí znovu.
    mail["fail"] = None
    second = await _notify(vehicle_id)
    assert second, "neúspěšný pokus se musí zopakovat"
    assert len(mail["sent"]) == len(second)

    rows = await _rows(vehicle_id)
    assert all(row.emailed_at is not None for row in rows)
    assert all(row.email_error is None for row in rows), "chyba se po úspěchu smaže"
    assert all(row.email_attempts == 2 for row in rows)

    # A potřetí už ne.
    delivered = len(mail["sent"])
    assert await _notify(vehicle_id) == []
    assert len(mail["sent"]) == delivered


async def test_retry_does_not_create_a_second_row(logged_in_client, csrf_token, mail):
    """Opakuje se odeslání, ne zpráva - schránka se nesmí zaplevelit."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-03", license_plate="1DL 0003",
    )

    mail["fail"] = "SMTP není nakonfigurováno"
    await _notify(vehicle_id)
    after_first = len(await _rows(vehicle_id))

    await _notify(vehicle_id)
    await _notify(vehicle_id)
    assert len(await _rows(vehicle_id)) == after_first

    rows = await _rows(vehicle_id)
    assert all(row.email_attempts == 3 for row in rows)


async def test_failure_is_written_to_the_log(logged_in_client, csrf_token, mail, caplog):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-04", license_plate="1DL 0004",
    )
    mail["fail"] = "Authentication failed"

    with caplog.at_level("WARNING", logger="app.modules.notifications.service"):
        await _notify(vehicle_id)

    assert any("Authentication failed" in record.getMessage() for record in caplog.records), caplog.text


# --- souběh -------------------------------------------------------------

async def test_two_concurrent_runs_send_one_mail(logged_in_client, csrf_token, mail):
    """Dva běhy plánovače naráz nesmí poslat dva e-maily.

    Chrání to částečný unikátní index (zakládání) a FOR UPDATE SKIP
    LOCKED (opakování) - obojí v databázi, protože kontrola v aplikaci
    by mezi dotazem a zápisem měla okno."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-10", license_plate="1DL 0010",
    )

    results = await asyncio.gather(
        _notify(vehicle_id), _notify(vehicle_id), return_exceptions=True,
    )
    for result in results:
        assert not isinstance(result, Exception), result

    rows = await _rows(vehicle_id)
    per_user = {}
    for row in rows:
        per_user[row.user_id] = per_user.get(row.user_id, 0) + 1
    assert all(count == 1 for count in per_user.values()), f"duplicitní řádky: {per_user}"

    # A hlavně: každý příjemce dostal nejvýš jeden e-mail.
    addressed = [email for email, subject in mail["sent"] if "1DL 0010" in subject]
    assert len(addressed) == len(set(addressed)), f"duplicitní e-maily: {addressed}"
    assert len(addressed) == len(rows)


async def test_concurrent_retry_of_a_failed_one_sends_once(logged_in_client, csrf_token, mail):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-11", license_plate="1DL 0011",
    )

    mail["fail"] = "Connection refused"
    await _notify(vehicle_id)
    assert mail["sent"] == []

    mail["fail"] = None
    results = await asyncio.gather(
        _notify(vehicle_id), _notify(vehicle_id), return_exceptions=True,
    )
    for result in results:
        assert not isinstance(result, Exception), result

    addressed = [email for email, subject in mail["sent"] if "1DL 0011" in subject]
    assert len(addressed) == len(set(addressed)), f"duplicitní e-maily: {addressed}"


# --- víc příjemců, různý výsledek ---------------------------------------

async def test_one_recipient_succeeds_the_other_fails(
    logged_in_client, csrf_token, admin_user, responsible_user, mail, monkeypatch,
):
    """Selhání u jednoho příjemce nesmí připravit o zprávu druhého ani
    označit jeho jako doručenou."""
    from app.core.db import async_session_factory
    from app.models.core import User

    async with async_session_factory() as db:
        owner = (await db.execute(
            select(User).where(User.email == responsible_user[0])
        )).scalar_one()

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-20", license_plate="1DL 0020",
        responsible_user_id=str(owner.id),
    )

    # Pošta selhává jen pro jednu konkrétní adresu.
    from app.core import mailer

    async def selective(config, to_email, subject, body):
        if to_email == owner.email:
            return "Mailbox unavailable"
        mail["sent"].append((to_email, subject))
        return None

    monkeypatch.setattr(mailer, "send_mail", selective)
    await _notify(vehicle_id)

    rows = {row.user_id: row for row in await _rows(vehicle_id)}
    assert rows[owner.id].email_status == "failed"
    assert rows[owner.id].email_error == "Mailbox unavailable"
    others = [row for user_id, row in rows.items() if user_id != owner.id]
    assert others, "měl být aspoň jeden další příjemce"
    assert all(row.email_status == "sent" for row in others)

    # Další běh zopakuje jen toho neúspěšného.
    monkeypatch.setattr(mailer, "send_mail", _recording(mail))
    retried = await _notify(vehicle_id)
    assert [n.user_id for n in retried] == [owner.id]
    assert [email for email, _ in mail["sent"] if email == owner.email] == [owner.email]


def _recording(state):
    async def fake_send(config, to_email, subject, body):
        state["sent"].append((to_email, subject))
        return None
    return fake_send


# --- preference mají přednost před vším ---------------------------------

async def test_disabled_preference_creates_nothing_to_deliver(
    logged_in_client, csrf_token, admin_user, responsible_user, mail,
):
    """Vypnutá volba znamená, že se zpráva ani nepřipraví - není tedy co
    opakovat, ani kdyby pošta selhala."""
    from app.core.db import async_session_factory
    from app.models.core import User
    from app.modules.notifications import preferences

    async with async_session_factory() as db:
        owner = (await db.execute(
            select(User).where(User.email == responsible_user[0])
        )).scalar_one()
        await preferences.save(db, owner.id, {"vehicle_deadlines": False})

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="DL-30", license_plate="1DL 0030",
        responsible_user_id=str(owner.id),
    )

    mail["fail"] = "Connection refused"
    await _notify(vehicle_id)

    rows = await _rows(vehicle_id)
    assert owner.id not in {row.user_id for row in rows}, "vypnuto = nevznikne ani řádek"

    # Ani opakovaný běh na tom nic nezmění.
    mail["fail"] = None
    await _notify(vehicle_id)
    assert owner.email not in [email for email, _ in mail["sent"]]

    async with async_session_factory() as db:
        await preferences.save(db, owner.id, {"vehicle_deadlines": True})


# --- notifikace bez klíče se chovají jako dřív --------------------------

async def test_notifications_without_a_dedupe_key_are_never_deduplicated(mail):
    """Rezervace, závady a schvalování se opakovat smí a mají."""
    from app.core.db import async_session_factory
    from app.modules.notifications import service as notifications

    recipient = await _user_first()
    async with async_session_factory() as db:
        first = await notifications.create(
            db, user=recipient, vehicle=None, kind="reservation",
            title="Zkouška bez klíče", body="tělo",
        )
        second = await notifications.create(
            db, user=recipient, vehicle=None, kind="reservation",
            title="Zkouška bez klíče", body="tělo",
        )

    assert first is not None and second is not None
    assert first.id != second.id
