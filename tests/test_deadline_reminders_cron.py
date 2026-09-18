"""Naplánované připomínky termínů respektují nastavení příjemců.

Rozdíl proti `test_notification_preferences.py`: tam se testují
jednotlivé funkce, tady jde všechno přes **`scripts.send_deadline_reminders.run()`**,
tedy přesně ten vstupní bod, který každé ráno v 7:00 spouští cron.

A netvrdí se „vznikl řádek", ale **„odešel e-mail na tuhle adresu"** -
`mailer.send_mail` je podstrčený a zaznamenává, komu se posílalo. To je
ta věc, na které záleží: kdo si připomínky vypnul, nesmí dostat poštu.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import create_vehicle, login


@pytest.fixture
def sent_mail(monkeypatch):
    """Zachytí každé volání odesílání pošty.

    Podstrkuje se atribut na modulu `app.core.mailer`, protože
    notifications/service.py volá `mailer.send_mail(...)` - tedy přes
    modul, ne přes dřív navázané jméno."""
    from app.core import mailer

    recorded: list[tuple[str, str]] = []

    async def fake_send(to_email: str, subject: str, body: str):
        recorded.append((to_email, subject))
        return None  # None = odesláno bez chyby

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return recorded


def _to(recorded, plate: str) -> set[str]:
    """Adresy, na které odešla pošta týkající se tohohle vozidla."""
    return {email for email, subject in recorded if plate in subject}


async def _user(email: str):
    from app.core.db import async_session_factory
    from app.models.core import User

    async with async_session_factory() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def _set_pref(user_id, code: str, enabled: bool) -> None:
    from app.core.db import async_session_factory
    from app.modules.notifications import preferences

    async with async_session_factory() as db:
        await preferences.save(db, user_id, {code: enabled})


async def _vehicle_with_expiring_stk(ac, csrf, *, code: str, plate: str, owner_id=None) -> str:
    """Vozidlo s STK za 5 dní - tedy oranžová, tedy připomínka odejde."""
    overrides = {
        "internal_code": code,
        "license_plate": plate,
        "stk_valid_until": (date.today() + timedelta(days=5)).isoformat(),
    }
    if owner_id is not None:
        overrides["responsible_user_id"] = str(owner_id)
    return await create_vehicle(ac, csrf, **overrides)


async def _run_cron(*, dry_run: bool = False) -> int:
    """Totéž, co spouští /etc/cron.d/kniha-jizd."""
    from scripts.send_deadline_reminders import run
    return await run(dry_run=dry_run)


# ======================================================================
# 1 + 3: zapnuto -> e-mail dorazí
# ======================================================================

async def test_responsible_person_and_admin_with_the_option_on_get_mail(
    logged_in_client, csrf_token, admin_user, responsible_user, sent_mail,
):
    owner = await _user(responsible_user[0])
    admin = await _user(admin_user[0])
    await _set_pref(owner.id, "vehicle_deadlines", True)
    await _set_pref(admin.id, "vehicle_deadlines", True)

    plate = "1CR 0001"
    await _vehicle_with_expiring_stk(
        logged_in_client, csrf_token, code="CR-01", plate=plate, owner_id=owner.id,
    )
    await _run_cron()

    addressed = _to(sent_mail, plate)
    assert owner.email in addressed, "odpovědná osoba se zapnutou volbou musí dostat e-mail"
    assert admin.email in addressed, "administrátor se zapnutou volbou musí dostat e-mail"


# ======================================================================
# 2: odpovědná osoba vypnuto -> nic
# ======================================================================

async def test_responsible_person_with_the_option_off_gets_nothing(
    logged_in_client, csrf_token, admin_user, responsible_user, sent_mail,
):
    owner = await _user(responsible_user[0])
    admin = await _user(admin_user[0])
    await _set_pref(owner.id, "vehicle_deadlines", False)
    await _set_pref(admin.id, "vehicle_deadlines", True)

    plate = "1CR 0002"
    await _vehicle_with_expiring_stk(
        logged_in_client, csrf_token, code="CR-02", plate=plate, owner_id=owner.id,
    )
    await _run_cron()

    addressed = _to(sent_mail, plate)
    assert owner.email not in addressed
    # 5: vypnutí jednoho se druhého nedotklo.
    assert admin.email in addressed

    await _set_pref(owner.id, "vehicle_deadlines", True)


# ======================================================================
# 4: admin vypnuto -> nic
# ======================================================================

async def test_admin_with_the_option_off_gets_nothing(
    logged_in_client, csrf_token, admin_user, responsible_user, sent_mail,
):
    owner = await _user(responsible_user[0])
    admin = await _user(admin_user[0])
    await _set_pref(admin.id, "vehicle_deadlines", False)
    await _set_pref(owner.id, "vehicle_deadlines", True)

    plate = "1CR 0003"
    await _vehicle_with_expiring_stk(
        logged_in_client, csrf_token, code="CR-03", plate=plate, owner_id=owner.id,
    )
    await _run_cron()

    addressed = _to(sent_mail, plate)
    assert admin.email not in addressed, "administrátor si volbu vypnout smí"
    # 5: odpovědné osobě to nic nevzalo.
    assert owner.email in addressed

    await _set_pref(admin.id, "vehicle_deadlines", True)


# ======================================================================
# 5: preference jednoho neovlivní druhého (obě strany naráz)
# ======================================================================

async def test_two_recipients_two_different_settings_one_event(
    logged_in_client, csrf_token, admin_user, responsible_user, sent_mail,
):
    owner = await _user(responsible_user[0])
    admin = await _user(admin_user[0])
    await _set_pref(owner.id, "vehicle_deadlines", True)
    await _set_pref(admin.id, "vehicle_deadlines", False)

    plate = "1CR 0004"
    await _vehicle_with_expiring_stk(
        logged_in_client, csrf_token, code="CR-04", plate=plate, owner_id=owner.id,
    )
    await _run_cron()

    # Nekontroluje se celá množina: administrátorů je v databázi víc a
    # ti ostatní připomínku dostávají právem. Podstatné je, že se tihle
    # dva rozešli přesně podle svých voleb.
    addressed = _to(sent_mail, plate)
    assert owner.email in addressed
    assert admin.email not in addressed

    await _set_pref(admin.id, "vehicle_deadlines", True)


# ======================================================================
# 7: žádné duplicity
# ======================================================================

async def test_admin_who_is_also_the_responsible_person_gets_one_mail(
    logged_in_client, csrf_token, admin_user, sent_mail,
):
    """Dva důvody k odeslání, jeden člověk, jeden e-mail."""
    admin = await _user(admin_user[0])
    await _set_pref(admin.id, "vehicle_deadlines", True)

    plate = "1CR 0005"
    await _vehicle_with_expiring_stk(
        logged_in_client, csrf_token, code="CR-05", plate=plate, owner_id=admin.id,
    )
    await _run_cron()

    to_admin = [email for email, subject in sent_mail if plate in subject and email == admin.email]
    assert len(to_admin) == 1, f"měl dostat jeden e-mail, dostal {len(to_admin)}"


async def test_second_run_the_same_day_sends_nothing(
    logged_in_client, csrf_token, admin_user, sent_mail,
):
    """Cron běží denně; opakovaný běh nesmí poslat totéž znovu.

    Tohle je pojistka i proti ručnímu spuštění navíc."""
    admin = await _user(admin_user[0])
    await _set_pref(admin.id, "vehicle_deadlines", True)

    plate = "1CR 0006"
    await _vehicle_with_expiring_stk(logged_in_client, csrf_token, code="CR-06", plate=plate)

    await _run_cron()
    after_first = len(_to(sent_mail, plate))
    assert after_first > 0

    sent_mail.clear()
    await _run_cron()
    assert _to(sent_mail, plate) == set(), "druhý běh už nesmí poslat nic"


async def test_dry_run_sends_nothing_at_all(logged_in_client, csrf_token, sent_mail):
    plate = "1CR 0007"
    await _vehicle_with_expiring_stk(logged_in_client, csrf_token, code="CR-07", plate=plate)

    await _run_cron(dry_run=True)
    assert sent_mail == []


async def test_vehicle_without_deadlines_is_left_alone(logged_in_client, csrf_token, sent_mail):
    """Nevyplněný termín není důvod komukoliv psát."""
    plate = "1CR 0008"
    await create_vehicle(logged_in_client, csrf_token, internal_code="CR-08", license_plate=plate)
    await _run_cron()
    assert _to(sent_mail, plate) == set()


# ======================================================================
# 6: rezervace respektují svou preferenci (taky na úrovni pošty)
# ======================================================================

async def test_reservation_mail_follows_the_recipients_choice(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user, sent_mail,
):
    from datetime import datetime, timezone
    from tests.conftest import extract_csrf_token

    owner = await _user(responsible_user[0])
    driver = await _user(basic_user[0])
    await _set_pref(owner.id, "reservation", False)
    await _set_pref(driver.id, "reservation", True)

    plate = "1CR 0010"
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="CR-10", license_plate=plate,
        responsible_user_id=str(owner.id),
    )

    await login(anon_client, basic_user)
    form = await anon_client.get("/kniha-jizd/reservations/new")
    start = datetime.now(timezone.utc) + timedelta(days=2)
    response = await anon_client.post(
        "/kniha-jizd/reservations/new",
        data={"csrf_token": extract_csrf_token(form.text), "vehicle_id": vehicle_id,
              "start_at": start.strftime("%Y-%m-%dT%H:%M"),
              "end_at": (start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
              "purpose": "Montáž"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    addressed = _to(sent_mail, plate)
    assert driver.email in addressed, "řidič má rezervace zapnuté"
    assert owner.email not in addressed, "odpovědná osoba si je vypnula"

    await _set_pref(owner.id, "reservation", True)


async def test_reservation_owner_who_is_also_responsible_gets_one_mail(
    logged_in_client, csrf_token, admin_user, sent_mail,
):
    """Majitel rezervace a odpovědná osoba v jedné osobě = jeden e-mail."""
    from datetime import datetime, timezone
    from tests.conftest import extract_csrf_token

    admin = await _user(admin_user[0])
    await _set_pref(admin.id, "reservation", True)

    plate = "1CR 0011"
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="CR-11", license_plate=plate,
        responsible_user_id=str(admin.id),
    )
    form = await logged_in_client.get("/kniha-jizd/reservations/new")
    start = datetime.now(timezone.utc) + timedelta(days=2)
    await logged_in_client.post(
        "/kniha-jizd/reservations/new",
        data={"csrf_token": extract_csrf_token(form.text), "vehicle_id": vehicle_id,
              "start_at": start.strftime("%Y-%m-%dT%H:%M"),
              "end_at": (start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
              "purpose": "Montáž"},
        follow_redirects=False,
    )

    to_admin = [email for email, subject in sent_mail if plate in subject and email == admin.email]
    assert len(to_admin) == 1, f"měl dostat jeden e-mail, dostal {len(to_admin)}"
