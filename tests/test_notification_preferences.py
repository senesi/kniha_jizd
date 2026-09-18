"""Individuální nastavení notifikací (zadání 20).

Podstata je v tom, že rozhodnutí „poslat" padá za každého příjemce
zvlášť. Testy proto skoro vždycky sledují dva lidi naráz a kontrolují,
že volba jednoho tomu druhému nic neudělala.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import notification_types
from app.core.fleet_status import DeadlineStatus
from tests.conftest import create_vehicle, extract_csrf_token, login


async def _session():
    from app.core.db import async_session_factory
    return async_session_factory()


async def _user_by_email(email: str):
    from app.core.db import async_session_factory
    from app.models.core import User

    async with async_session_factory() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def _notifications_for(user_id, *, kind: str | None = None) -> list:
    from app.core.db import async_session_factory
    from app.models.fleet import Notification

    async with async_session_factory() as db:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if kind is not None:
            stmt = stmt.where(Notification.kind == kind)
        return list((await db.execute(stmt.order_by(Notification.created_at))).scalars().all())


async def _prefs(user_id) -> dict:
    from app.core.db import async_session_factory
    from app.modules.notifications import preferences

    async with async_session_factory() as db:
        return await preferences.get_all(db, user_id)


async def _set_pref(user_id, code: str, enabled: bool) -> None:
    from app.core.db import async_session_factory
    from app.modules.notifications import preferences

    async with async_session_factory() as db:
        await preferences.save(db, user_id, {code: enabled})


# ======================================================================
# Katalog typů
# ======================================================================

def test_every_kind_belongs_to_exactly_one_type():
    """Kdyby jeden kind spadal pod dva přepínače, nikdo by nevěděl, který
    z nich ho vypíná."""
    seen = {}
    for notification_type in notification_types.TYPES:
        for kind in notification_type.kinds:
            assert kind not in seen, f"{kind} je ve dvou typech"
            seen[kind] = notification_type.code
    assert seen == notification_types.TYPE_CODE_BY_KIND


def test_unknown_kind_is_not_silently_swallowed():
    """Nezaregistrovaný druh zprávy se musí poslat, ne zmizet."""
    assert notification_types.type_for_kind("neco_uplne_noveho") is None


def test_required_types_exist_and_default_to_on():
    for code in ("reservation", "reservation_change", "vehicle_deadlines", "approval"):
        assert code in notification_types.TYPES_BY_CODE, code
        assert notification_types.DEFAULTS[code] is True, code


# ======================================================================
# Výchozí nastavení
# ======================================================================

async def test_new_user_gets_the_catalogue_defaults(logged_in_client, csrf_token):
    """Nově založený uživatel má vše zapnuté, aniž by mu kdo zakládal řádky."""
    email = f"novy-{uuid.uuid4().hex[:8]}@example.com"
    response = await logged_in_client.post(
        "/kniha-jizd/users/new",
        data={"csrf_token": csrf_token, "email": email, "full_name": "Nový Uživatel",
              "password": "TestPassword123!", "roles": "user"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    created = await _user_by_email(email)
    assert await _prefs(created.id) == notification_types.DEFAULTS


async def test_responsible_person_has_reservations_and_deadlines_on(responsible_user):
    person = await _user_by_email(responsible_user[0])
    prefs = await _prefs(person.id)
    assert prefs["reservation"] is True
    assert prefs["reservation_change"] is True
    assert prefs["vehicle_deadlines"] is True


async def test_admin_has_deadlines_on(admin_user):
    admin = await _user_by_email(admin_user[0])
    assert (await _prefs(admin.id))["vehicle_deadlines"] is True


async def test_existing_users_have_defined_defaults_without_rows(basic_user):
    """Migrace nic nebackfilluje - chybějící řádek znamená výchozí
    hodnotu, ne „vypnuto"."""
    from app.core.db import async_session_factory
    from app.models.core import UserNotificationPreference

    person = await _user_by_email(basic_user[0])
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(UserNotificationPreference).where(UserNotificationPreference.user_id == person.id)
        )).scalars().all()
    assert rows == []
    assert await _prefs(person.id) == notification_types.DEFAULTS


async def test_new_type_needs_no_migration(basic_user, monkeypatch):
    """Přidání typu do katalogu stačí - uživatelé bez řádku dostanou jeho
    výchozí hodnotu okamžitě."""
    person = await _user_by_email(basic_user[0])
    extra = notification_types.NotificationType(
        code="zkusebni_typ", label="Zkušební", description="jen test",
        default_enabled=False, kinds=("zkusebni_kind",),
    )
    monkeypatch.setitem(notification_types.TYPES_BY_CODE, extra.code, extra)
    monkeypatch.setitem(notification_types.DEFAULTS, extra.code, extra.default_enabled)
    monkeypatch.setitem(notification_types.TYPE_CODE_BY_KIND, "zkusebni_kind", extra.code)

    prefs = await _prefs(person.id)
    assert prefs["zkusebni_typ"] is False

    from app.core.db import async_session_factory
    from app.modules.notifications import preferences
    async with async_session_factory() as db:
        assert await preferences.is_enabled(db, person.id, kind="zkusebni_kind") is False


# ======================================================================
# Obrazovka
# ======================================================================

async def test_settings_page_lists_every_type(logged_in_client):
    page = await logged_in_client.get("/kniha-jizd/account/notifications")
    assert page.status_code == 200
    for notification_type in notification_types.TYPES:
        assert notification_type.label in page.text
        assert notification_type.description in page.text
        assert f'name="type_{notification_type.code}"' in page.text


async def test_saving_turns_a_type_off_and_back_on(logged_in_client, admin_user):
    admin = await _user_by_email(admin_user[0])
    page = await logged_in_client.get("/kniha-jizd/account/notifications")
    csrf = extract_csrf_token(page.text)

    # Nezaškrtnuté zaškrtávátko prohlížeč neposílá - vypnutí je tedy
    # nepřítomnost pole, ne "0".
    off = {"csrf_token": csrf}
    off.update({f"type_{t.code}": "1" for t in notification_types.TYPES if t.code != "defect"})
    assert (await logged_in_client.post(
        "/kniha-jizd/account/notifications", data=off, follow_redirects=False,
    )).status_code == 303
    assert (await _prefs(admin.id))["defect"] is False

    on = {"csrf_token": csrf}
    on.update({f"type_{t.code}": "1" for t in notification_types.TYPES})
    await logged_in_client.post("/kniha-jizd/account/notifications", data=on, follow_redirects=False)
    assert (await _prefs(admin.id))["defect"] is True


async def test_page_is_reachable_from_the_profile(logged_in_client):
    profile = await logged_in_client.get("/kniha-jizd/account/profile")
    assert "/kniha-jizd/account/notifications" in profile.text


async def test_nobody_can_save_settings_for_somebody_else(
    anon_client, basic_user, admin_user,
):
    """Cílový uživatel se nebere z formuláře, vždycky je to přihlášený."""
    admin = await _user_by_email(admin_user[0])
    await login(anon_client, basic_user)
    driver = await _user_by_email(basic_user[0])

    page = await anon_client.get("/kniha-jizd/account/notifications")
    data = {"csrf_token": extract_csrf_token(page.text), "user_id": str(admin.id)}
    # Vše odškrtnuto -> vypnout všechno.
    await anon_client.post("/kniha-jizd/account/notifications", data=data, follow_redirects=False)

    assert all(value is False for value in (await _prefs(driver.id)).values())
    # Adminovi se nic nestalo.
    assert (await _prefs(admin.id))["reservation"] is True


# ======================================================================
# Preference se skutečně projeví při odesílání
# ======================================================================

async def _reserve(ac, vehicle_id, *, days_ahead=1, **fields):
    """Rezervaci zakládá vždycky ten, kdo je přihlášený - formulář
    neumí „za někoho jiného", takže majitel = zakladatel."""
    form = await ac.get("/kniha-jizd/reservations/new")
    start = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    data = {
        "csrf_token": extract_csrf_token(form.text),
        "vehicle_id": vehicle_id,
        "start_at": start.strftime("%Y-%m-%dT%H:%M"),
        "end_at": (start + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
        "purpose": "Montáž",
        **fields,
    }
    response = await ac.post("/kniha-jizd/reservations/new", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/reservations/")[1].split("?")[0]


async def test_responsible_person_is_notified_about_a_reservation(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user,
):
    owner = await _user_by_email(responsible_user[0])
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-01", license_plate="1NP 0001",
        responsible_user_id=str(owner.id),
    )

    await login(anon_client, basic_user)
    await _reserve(anon_client, vehicle_id)

    # Odpovědná osoba vozidla i řidič, který rezervoval - každý jednou.
    assert len(await _notifications_for(owner.id, kind="reservation")) == 1
    driver = await _user_by_email(basic_user[0])
    assert len(await _notifications_for(driver.id, kind="reservation")) == 1


async def test_turning_reservations_off_stops_them(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user,
):
    owner = await _user_by_email(responsible_user[0])
    await _set_pref(owner.id, "reservation", False)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-02", license_plate="1NP 0002",
        responsible_user_id=str(owner.id),
    )
    before = len(await _notifications_for(owner.id, kind="reservation"))

    await login(anon_client, basic_user)
    await _reserve(anon_client, vehicle_id)

    assert len(await _notifications_for(owner.id, kind="reservation")) == before
    await _set_pref(owner.id, "reservation", True)


async def test_one_users_choice_does_not_affect_another(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user, admin_user,
):
    """Dva příjemci téže události, každý s vlastním nastavením."""
    owner = await _user_by_email(responsible_user[0])
    driver = await _user_by_email(basic_user[0])

    # Rezervace se týká majitele rezervace i odpovědné osoby vozidla.
    await _set_pref(owner.id, "reservation", False)
    await _set_pref(driver.id, "reservation", True)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-03", license_plate="1NP 0003",
        responsible_user_id=str(owner.id),
    )
    owner_before = len(await _notifications_for(owner.id, kind="reservation"))
    driver_before = len(await _notifications_for(driver.id, kind="reservation"))

    # Táž událost, dva příjemci: odpovědná osoba a řidič, který rezervoval.
    await login(anon_client, basic_user)
    await _reserve(anon_client, vehicle_id)

    assert len(await _notifications_for(owner.id, kind="reservation")) == owner_before
    assert len(await _notifications_for(driver.id, kind="reservation")) == driver_before + 1

    await _set_pref(owner.id, "reservation", True)


async def test_changing_and_cancelling_a_reservation_notifies(
    logged_in_client, csrf_token, anon_client, basic_user,
):
    driver = await _user_by_email(basic_user[0])
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-04", license_plate="1NP 0004",
    )
    await login(anon_client, basic_user)
    reservation_id = await _reserve(anon_client, vehicle_id)
    before = len(await _notifications_for(driver.id, kind="reservation_change"))

    start = datetime.now(timezone.utc) + timedelta(days=3)
    await logged_in_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/edit",
        data={"csrf_token": csrf_token,
              "start_at": start.strftime("%Y-%m-%dT%H:%M"),
              "end_at": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
              "purpose": "Posunuto"},
        follow_redirects=False,
    )
    await logged_in_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/cancel",
        data={"csrf_token": csrf_token, "reason": "Nepojede se"}, follow_redirects=False,
    )

    after = await _notifications_for(driver.id, kind="reservation_change")
    assert len(after) == before + 2
    assert "Zrušená rezervace" in after[-1].title


async def test_turning_reservation_changes_off_keeps_new_reservations_on(
    logged_in_client, csrf_token, anon_client, basic_user,
):
    """Dva samostatné přepínače - vypnutí jednoho nesmí vypnout druhý."""
    driver = await _user_by_email(basic_user[0])
    await _set_pref(driver.id, "reservation_change", False)
    await _set_pref(driver.id, "reservation", True)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-05", license_plate="1NP 0005",
    )
    created_before = len(await _notifications_for(driver.id, kind="reservation"))
    changed_before = len(await _notifications_for(driver.id, kind="reservation_change"))

    await login(anon_client, basic_user)
    reservation_id = await _reserve(anon_client, vehicle_id)
    await logged_in_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/cancel",
        data={"csrf_token": csrf_token, "reason": "Test"}, follow_redirects=False,
    )

    assert len(await _notifications_for(driver.id, kind="reservation")) == created_before + 1
    assert len(await _notifications_for(driver.id, kind="reservation_change")) == changed_before
    await _set_pref(driver.id, "reservation_change", True)


# ======================================================================
# Termíny vozidla
# ======================================================================

def _red_deadline() -> DeadlineStatus:
    return DeadlineStatus(
        code="stk", label="STK", level="red",
        due_date=date.today() + timedelta(days=3), days_left=3,
        detail="STK končí za 3 dny.",
    )


async def test_deadline_reaches_responsible_person_and_admins(
    logged_in_client, csrf_token, responsible_user, admin_user,
):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    owner = await _user_by_email(responsible_user[0])
    admin = await _user_by_email(admin_user[0])
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-10", license_plate="1NP 0010",
        responsible_user_id=str(owner.id),
    )

    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        sent = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=_red_deadline())

    recipients = {notification.user_id for notification in sent}
    assert owner.id in recipients
    assert admin.id in recipients


async def test_deadline_respects_each_recipients_choice(
    logged_in_client, csrf_token, responsible_user, admin_user,
):
    """Admin si termíny vypne - odpovědné osobě to nesmí vzít."""
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    owner = await _user_by_email(responsible_user[0])
    admin = await _user_by_email(admin_user[0])
    await _set_pref(admin.id, "vehicle_deadlines", False)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-11", license_plate="1NP 0011",
        responsible_user_id=str(owner.id),
    )
    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        sent = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=_red_deadline())

    recipients = {notification.user_id for notification in sent}
    assert owner.id in recipients
    assert admin.id not in recipients

    await _set_pref(admin.id, "vehicle_deadlines", True)


async def test_deadline_is_not_sent_twice(logged_in_client, csrf_token, responsible_user):
    """Denní běh nesmí posílat totéž pořád dokola."""
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    owner = await _user_by_email(responsible_user[0])
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-12", license_plate="1NP 0012",
        responsible_user_id=str(owner.id),
    )
    deadline = _red_deadline()

    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        first = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=deadline)
        second = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=deadline)

    assert first, "první běh musí něco poslat"
    assert second == [], "druhý běh už nic"

    # Posunutý termín ale připomínku pustí znovu.
    moved = DeadlineStatus(
        code="stk", label="STK", level="red",
        due_date=date.today() + timedelta(days=370), days_left=370, detail="Nová STK.",
    )
    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        assert await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=moved)


async def test_green_deadline_is_not_sent_at_all(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-13", license_plate="1NP 0013",
    )
    green = DeadlineStatus(code="stk", label="STK", level="green", detail="V pořádku.")
    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        assert await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=green) == []


async def test_changing_the_responsible_person_uses_the_new_ones_settings(
    logged_in_client, csrf_token, responsible_user, basic_user,
):
    """Nová odpovědná osoba nedědí nic po předchozí - platí její volby."""
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    first_owner = await _user_by_email(responsible_user[0])
    second_owner = await _user_by_email(basic_user[0])
    await _set_pref(first_owner.id, "vehicle_deadlines", True)
    await _set_pref(second_owner.id, "vehicle_deadlines", False)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-14", license_plate="1NP 0014",
        responsible_user_id=str(first_owner.id),
    )

    # Předání vozidla druhé osobě.
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/edit")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={"csrf_token": extract_csrf_token(form.text), "internal_code": "N-14",
              "license_plate": "1NP 0014", "brand": "Škoda", "model": "Octavia",
              "vehicle_type": "osobni", "fuel_type": "nafta", "status": "available",
              "is_active": "1", "current_odometer_km": "100000",
              "responsible_user_id": str(second_owner.id)},
        follow_redirects=False,
    )

    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        assert vehicle.responsible_user_id == second_owner.id
        sent = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=_red_deadline())

    recipients = {notification.user_id for notification in sent}
    # Nová odpovědná osoba si termíny vypnula, takže nic nedostane...
    assert second_owner.id not in recipients
    # ...a bývalá už není odpovědná, takže taky ne (pokud není admin).
    assert first_owner.id not in recipients

    await _set_pref(second_owner.id, "vehicle_deadlines", True)


async def test_nobody_gets_the_same_deadline_twice_in_one_run(
    logged_in_client, csrf_token, admin_user,
):
    """Administrátor, který je zároveň odpovědnou osobou, je v seznamu
    příjemců jednou, ne dvakrát."""
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle
    from app.modules.notifications import service as notifications

    admin = await _user_by_email(admin_user[0])
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="N-15", license_plate="1NP 0015",
        responsible_user_id=str(admin.id),
    )
    async with async_session_factory() as db:
        vehicle = (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()
        recipients = await notifications.deadline_recipients(db, vehicle)

    ids = [recipient.id for recipient in recipients]
    assert ids.count(admin.id) == 1
