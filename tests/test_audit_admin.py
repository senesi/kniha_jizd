"""Administrace → Audit / Aktivita (zadání 26/30).

Kromě toho, že obrazovka funguje, se tu hlídají dvě věci, u kterých je
chyba nevratná: že se do auditu nedostane žádné tajemství (zpětně se to
neopraví, je v zálohách) a že audit nejde přes UI měnit.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from tests.conftest import TEST_PASSWORD, create_vehicle, extract_csrf_token, login


async def _entries(**where) -> list:
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    async with async_session_factory() as db:
        stmt = select(AuditLog)
        for column, value in where.items():
            stmt = stmt.where(getattr(AuditLog, column) == value)
        return list((await db.execute(stmt.order_by(AuditLog.created_at.desc()))).scalars().all())


# ======================================================================
# Přístup
# ======================================================================

async def test_admin_sees_the_audit(logged_in_client):
    page = await logged_in_client.get("/kniha-jizd/admin/audit")
    assert page.status_code == 200
    assert "Audit a aktivita" in page.text


async def test_driver_does_not_see_the_audit(anon_client, basic_user):
    """Ani odkaz, ani přímá URL - kontrola je v dependency routy."""
    await login(anon_client, basic_user)

    dashboard = await anon_client.get("/kniha-jizd/")
    assert "/kniha-jizd/admin/audit" not in dashboard.text

    assert (await anon_client.get("/kniha-jizd/admin/audit")).status_code == 403
    assert (await anon_client.get(f"/kniha-jizd/admin/audit/{uuid.uuid4()}")).status_code == 403


async def test_responsible_person_does_not_see_the_audit(anon_client, responsible_user):
    await login(anon_client, responsible_user)
    assert (await anon_client.get("/kniha-jizd/admin/audit")).status_code == 403


# ======================================================================
# Audit je read-only
# ======================================================================

def test_audit_module_has_no_write_routes():
    """Append-only není vlastnost šablony: cesta zpátky neexistuje."""
    from app.main import app

    audit_routes = [r for r in app.routes if "/admin/audit" in getattr(r, "path", "")]
    assert audit_routes, "routy auditu musí existovat"
    for route in audit_routes:
        assert route.methods <= {"GET", "HEAD"}, f"{route.path}: {route.methods}"


def test_audit_repository_exposes_no_mutations():
    from app.modules.audit import repository

    forbidden = [name for name in dir(repository)
                 if any(word in name for word in ("delete", "update", "purge", "set_", "save"))]
    assert forbidden == [], forbidden


async def test_writing_to_the_audit_url_is_refused(logged_in_client, csrf_token):
    await create_vehicle(logged_in_client, csrf_token, internal_code="AU-RO", license_plate="1AU 9999")
    entries = await _entries(module="vehicles")
    assert entries
    target = entries[0]

    for path in (f"/kniha-jizd/admin/audit/{target.id}", "/kniha-jizd/admin/audit"):
        response = await logged_in_client.post(path, data={"csrf_token": csrf_token}, follow_redirects=False)
        assert response.status_code == 405, path


# ======================================================================
# Co se zaznamenává
# ======================================================================

async def test_changing_a_vehicle_writes_an_audit_entry(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="AU-01", license_plate="1AU 0001",
    )
    entries = await _entries(module="vehicles", entity_id=vehicle_id)
    assert entries, "založení vozidla se musí zapsat"

    created = entries[-1]
    assert created.action == "create"
    assert created.user_id is not None
    assert str(created.vehicle_id) == vehicle_id, "vozidlo musí jít do vlastního sloupce"
    assert created.after_data


async def test_vehicle_column_is_filled_from_the_payload(logged_in_client, csrf_token):
    """Moduly dávaly vehicle_id do payloadu dávno před tím, než měl audit
    vlastní sloupec - nesmí se tím přijít o filtr."""
    from app.core.db import async_session_factory
    from app.core.audit import log_action
    from app.models.core import AuditLog, User

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="AU-02", license_plate="1AU 0002",
    )
    async with async_session_factory() as db:
        actor = (await db.execute(select(User).order_by(User.created_at))).scalars().first()
        await log_action(
            db, user_id=actor.id, action="update", module="trips", entity_type="trip",
            entity_id="x-1", after_data={"vehicle_id": vehicle_id, "note": "bez explicitního parametru"},
        )
        await db.commit()
        entry = (await db.execute(
            select(AuditLog).where(AuditLog.entity_id == "x-1")
        )).scalar_one()

    assert str(entry.vehicle_id) == vehicle_id


async def test_successful_login_is_recorded(anon_client, basic_user):
    await login(anon_client, basic_user)

    entries = await _entries(module="auth", action="login")
    assert entries
    latest = entries[0]
    assert latest.result == "success"
    assert latest.is_login_event
    assert basic_user[0] in (latest.description or "")


async def test_failed_login_is_recorded(anon_client):
    """Bez toho by nešlo poznat, že někdo zkouší hesla."""
    before = len(await _entries(module="auth", action="login_failed"))

    response = await anon_client.post(
        "/kniha-jizd/login",
        data={"email": "nikdo@example.com", "password": "spatne-heslo"},
        follow_redirects=False,
    )
    assert response.status_code == 401

    entries = await _entries(module="auth", action="login_failed")
    assert len(entries) == before + 1
    latest = entries[0]
    assert latest.result == "failure"
    assert latest.user_id is None, "neúspěšný pokus nemá koho přiřadit"
    assert "nikdo@example.com" in (latest.description or "")


async def test_logout_is_recorded(anon_client, basic_user):
    await login(anon_client, basic_user)
    before = len(await _entries(module="auth", action="logout"))

    await anon_client.get("/kniha-jizd/logout", follow_redirects=False)

    assert len(await _entries(module="auth", action="logout")) == before + 1


async def test_notification_preference_change_is_recorded(logged_in_client, admin_user):
    from app.core import notification_types

    page = await logged_in_client.get("/kniha-jizd/account/notifications")
    data = {"csrf_token": extract_csrf_token(page.text)}
    data.update({f"type_{t.code}": "1" for t in notification_types.TYPES if t.code != "trip"})
    await logged_in_client.post("/kniha-jizd/account/notifications", data=data, follow_redirects=False)

    entries = await _entries(module="notifications")
    assert entries
    assert entries[0].after_data.get("trip") is False


# ======================================================================
# Žádná tajemství v auditu
# ======================================================================

def test_scrub_removes_secrets():
    from app.core.audit import REDACTED, scrub

    cleaned = scrub({
        "email": "kdo@example.com",
        "password": "tajne",
        "new_password": "tajne2",
        "smtp_password": "postovni",
        "session_token": "abc",
        "csrf_token": "def",
        "nested": {"secret": "x", "ok": 1},
    })
    assert cleaned["email"] == "kdo@example.com"
    assert cleaned["nested"]["ok"] == 1
    for key in ("password", "new_password", "smtp_password", "session_token", "csrf_token"):
        assert cleaned[key] == REDACTED, key
    assert cleaned["nested"]["secret"] == REDACTED


async def test_password_change_never_lands_in_the_audit(logged_in_client, admin_user):
    new_password = "UplneNoveHeslo123!"
    page = await logged_in_client.get("/kniha-jizd/account/password")
    await logged_in_client.post(
        "/kniha-jizd/account/password",
        data={"csrf_token": extract_csrf_token(page.text),
              "current_password": TEST_PASSWORD,
              "new_password": new_password, "new_password_again": new_password},
        follow_redirects=False,
    )

    for entry in await _entries(module="users"):
        blob = f"{entry.before_data}{entry.after_data}{entry.description}"
        assert new_password not in blob
        assert TEST_PASSWORD not in blob
        assert "$argon2" not in blob, "hash hesla v auditu taky nemá co dělat"


async def test_smtp_password_never_lands_in_the_audit(logged_in_client):
    secret = "smtp-tajemstvi-987"
    page = await logged_in_client.get("/kniha-jizd/settings/mail")
    await logged_in_client.post(
        "/kniha-jizd/settings/mail",
        data={"csrf_token": extract_csrf_token(page.text), "smtp_host": "smtp.example.com",
              "smtp_port": "587", "smtp_user": "a@b.cz", "smtp_password": secret,
              "smtp_from": "x@y.cz", "smtp_starttls": "1"},
        follow_redirects=False,
    )

    entries = await _entries(entity_type="smtp")
    assert entries
    for entry in entries:
        assert secret not in f"{entry.before_data}{entry.after_data}{entry.description}"
    assert entries[0].after_data["password_changed"] is True


async def test_no_session_token_anywhere_in_the_audit(logged_in_client, anon_client, basic_user):
    await login(anon_client, basic_user)
    cookie = anon_client.cookies.get("kniha_jizd_session")
    assert cookie, "test potřebuje skutečnou session cookie"

    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    async with async_session_factory() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
        for entry in rows:
            blob = f"{entry.before_data}{entry.after_data}{entry.description}{entry.user_agent}"
            assert cookie not in blob


# ======================================================================
# Filtry a stránkování
# ======================================================================

async def test_filters_narrow_the_result(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="AU-10", license_plate="1AU 0010",
    )
    entry = (await _entries(module="vehicles", entity_id=vehicle_id))[-1]

    # Modul
    only_vehicles = await logged_in_client.get("/kniha-jizd/admin/audit?module=vehicles")
    assert str(entry.id) in only_vehicles.text
    only_auth = await logged_in_client.get("/kniha-jizd/admin/audit?module=auth")
    assert str(entry.id) not in only_auth.text

    # Vozidlo
    by_vehicle = await logged_in_client.get(f"/kniha-jizd/admin/audit?vehicle={vehicle_id}")
    assert str(entry.id) in by_vehicle.text

    # Akce
    by_action = await logged_in_client.get("/kniha-jizd/admin/audit?action=create&module=vehicles")
    assert str(entry.id) in by_action.text
    other_action = await logged_in_client.get("/kniha-jizd/admin/audit?action=delete&module=vehicles")
    assert str(entry.id) not in other_action.text

    # Období, které záznam nemůže obsahovat
    old_period = await logged_in_client.get("/kniha-jizd/admin/audit?from=2020-01-01&to=2020-01-31")
    assert str(entry.id) not in old_period.text

    # Dnešek ho obsahovat musí
    today = date.today().isoformat()
    current = await logged_in_client.get(f"/kniha-jizd/admin/audit?from={today}&to={today}&vehicle={vehicle_id}")
    assert str(entry.id) in current.text


async def test_login_filters(logged_in_client, anon_client, basic_user):
    await login(anon_client, basic_user)
    await anon_client.post(
        "/kniha-jizd/login", data={"email": "nikdo2@example.com", "password": "x"},
        follow_redirects=False,
    )

    def has_failed_login(html: str) -> bool:
        """Je neúspěšné přihlášení mezi vypsanými záznamy?

        Pouhé „je v textu" nestačí: popisek je i v rozbalovacím filtru
        akcí, a to právě jednou. Víc než jeden výskyt tedy znamená, že
        je i ve výpisu."""
        return html.count("Neúspěšné přihlášení") > 1

    logins = await logged_in_client.get("/kniha-jizd/admin/audit?events=logins")
    assert has_failed_login(logins.text)

    failures = await logged_in_client.get("/kniha-jizd/admin/audit?events=logins&result=failure")
    assert has_failed_login(failures.text)

    changes = await logged_in_client.get("/kniha-jizd/admin/audit?events=changes")
    assert not has_failed_login(changes.text)


async def test_search_finds_by_description(logged_in_client, anon_client, basic_user):
    await login(anon_client, basic_user)
    found = await logged_in_client.get(f"/kniha-jizd/admin/audit?q={basic_user[0]}")
    assert basic_user[0] in found.text

    nothing = await logged_in_client.get("/kniha-jizd/admin/audit?q=rozhodne-neexistujici-retezec")
    assert "neodpovídá žádný záznam" in nothing.text


async def test_broken_filter_params_do_not_crash(logged_in_client):
    page = await logged_in_client.get(
        "/kniha-jizd/admin/audit?from=vcera&user=neni-uuid&vehicle=x&result=mozna&page=-5"
    )
    assert page.status_code == 200


async def test_pagination(logged_in_client, csrf_token):
    from app.modules.audit.filters import PAGE_SIZE

    total = int((await logged_in_client.get("/kniha-jizd/admin/audit")).text
                .split("záznamů")[0].rsplit(">", 1)[-1].strip() or 0)
    # Doplnit záznamy tak, aby stránek bylo víc než jedna.
    index = 0
    while total <= PAGE_SIZE:
        await create_vehicle(
            logged_in_client, csrf_token,
            internal_code=f"AU-P{index}", license_plate=f"1AP {index:04d}",
        )
        index += 1
        total += 1

    first = await logged_in_client.get("/kniha-jizd/admin/audit")
    assert "strana 1 z" in first.text
    # Každý záznam je v HTML dvakrát: tabulka pro desktop a karta pro mobil.
    assert first.text.count("/kniha-jizd/admin/audit/") <= PAGE_SIZE * 2 + 2

    second = await logged_in_client.get("/kniha-jizd/admin/audit?page=2")
    assert second.status_code == 200
    assert "strana 2 z" in second.text


async def test_detail_shows_both_payloads(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="AU-20", license_plate="1AU 0020",
    )
    entry = (await _entries(module="vehicles", entity_id=vehicle_id))[-1]

    detail = await logged_in_client.get(f"/kniha-jizd/admin/audit/{entry.id}")
    assert detail.status_code == 200
    assert "Původní hodnoty" in detail.text
    assert "Nové hodnoty" in detail.text
    assert "1AU 0020" in detail.text


async def test_unknown_detail_is_404(logged_in_client):
    assert (await logged_in_client.get(f"/kniha-jizd/admin/audit/{uuid.uuid4()}")).status_code == 404
