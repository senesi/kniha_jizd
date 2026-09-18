"""Nastavení odesílání e-mailů v administraci (zadání 20).

Dvě věci, na kterých tu záleží nejvíc: že se uložené heslo nikdy
nedostane zpátky do prohlížeče, a že v databázi neleží čitelné — jinak
by skončilo i v zálohách `pg_dump`.
"""
import pytest
from sqlalchemy import select

from app.core import app_settings, crypto
from tests.conftest import extract_csrf_token, login

SECRET = "tajne-heslo-do-posty-123"


async def _row(key: str) -> str | None:
    from app.core.db import async_session_factory
    from app.models.core import AppSetting

    async with async_session_factory() as db:
        return (await db.execute(
            select(AppSetting.value).where(AppSetting.key == key)
        )).scalar_one_or_none()


async def _config():
    from app.core.db import async_session_factory

    async with async_session_factory() as db:
        return await app_settings.get_smtp(db)


async def _save(ac, **overrides):
    page = await ac.get("/kniha-jizd/settings/mail")
    data = {
        "csrf_token": extract_csrf_token(page.text),
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_user": "kniha@example.com",
        "smtp_from": "kniha-jizd@example.com",
        "smtp_starttls": "1",
    }
    data.update(overrides)
    data = {k: v for k, v in data.items() if v is not None}
    return await ac.post("/kniha-jizd/settings/mail", data=data, follow_redirects=False)


# --- šifrování ---------------------------------------------------------

def test_secret_round_trip():
    token = crypto.encrypt_secret(SECRET)
    assert token != SECRET
    assert crypto.is_encrypted(token)
    assert crypto.decrypt_secret(token) == SECRET


def test_empty_secret_stays_empty():
    """Prázdno není tajemství, je to „nenastaveno"."""
    assert crypto.encrypt_secret("") == ""
    assert crypto.decrypt_secret("") is None
    assert crypto.decrypt_secret(None) is None


def test_undecryptable_value_is_none_not_a_crash():
    """Po výměně SESSION_SECRET_KEY se heslo přečíst nedá - aplikace to
    musí přežít a říct si o nové."""
    assert crypto.decrypt_secret("enc:v1:tohle-neni-platny-token") is None


def test_value_written_by_hand_is_taken_as_is():
    """Aby šlo nastavení opravit zvenčí, když je zle."""
    assert crypto.decrypt_secret("rucne-zapsane") == "rucne-zapsane"


# --- přístup -----------------------------------------------------------

async def test_only_settings_manager_gets_in(anon_client, basic_user):
    await login(anon_client, basic_user)
    assert (await anon_client.get("/kniha-jizd/settings/mail")).status_code == 403
    assert (await anon_client.post(
        "/kniha-jizd/settings/mail", data={}, follow_redirects=False,
    )).status_code == 403


async def test_page_is_reachable_from_settings(logged_in_client):
    settings_page = await logged_in_client.get("/kniha-jizd/settings")
    assert "/kniha-jizd/settings/mail" in settings_page.text


# --- ukládání ----------------------------------------------------------

async def test_saving_stores_the_password_encrypted(logged_in_client):
    assert (await _save(logged_in_client, smtp_password=SECRET)).status_code == 303

    stored = await _row(app_settings.SMTP_PASSWORD_KEY)
    assert stored is not None
    # Tohle je ta podstatná aserce: v databázi (a tedy i v pg_dump záloze)
    # heslo čitelné není.
    assert SECRET not in stored
    assert crypto.is_encrypted(stored)

    config = await _config()
    assert config.password == SECRET
    assert config.host == "smtp.example.com"
    assert config.port == 587
    assert config.is_configured
    assert config.from_database


async def test_password_never_comes_back_to_the_browser(logged_in_client):
    await _save(logged_in_client, smtp_password=SECRET)

    page = await logged_in_client.get("/kniha-jizd/settings/mail")
    assert page.status_code == 200
    assert SECRET not in page.text
    # Ani zašifrovaná podoba nemá co dělat v HTML.
    assert "enc:v1:" not in page.text
    # Obrazovka jen prozradí, že nějaké heslo uložené je.
    assert "uloženo" in page.text


async def test_empty_password_field_keeps_the_stored_one(logged_in_client):
    """Formulář heslo nevypisuje, takže prázdné pole ho nesmí smazat."""
    await _save(logged_in_client, smtp_password=SECRET)
    await _save(logged_in_client, smtp_user="jiny@example.com", smtp_password=None)

    config = await _config()
    assert config.user == "jiny@example.com"
    assert config.password == SECRET


async def test_starttls_can_be_turned_off_and_on(logged_in_client):
    await _save(logged_in_client, smtp_starttls=None)
    assert (await _config()).starttls is False

    await _save(logged_in_client, smtp_starttls="1")
    assert (await _config()).starttls is True


async def test_nonsense_port_is_refused(logged_in_client):
    for port in ("0", "70000", "abc"):
        response = await _save(logged_in_client, smtp_port=port)
        assert response.status_code == 400, port
        assert "Port" in response.text


async def test_audit_records_the_change_but_never_the_password(logged_in_client, admin_user):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    await _save(logged_in_client, smtp_password=SECRET)

    async with async_session_factory() as db:
        entry = (await db.execute(
            select(AuditLog).where(AuditLog.entity_type == "smtp")
            .order_by(AuditLog.created_at.desc())
        )).scalars().first()

    assert entry is not None
    assert entry.after_data["host"] == "smtp.example.com"
    assert entry.after_data["password_changed"] is True
    # Heslo ani jeho délka do auditu nepatří.
    assert SECRET not in str(entry.after_data)


# --- vazba na odesílání -------------------------------------------------

async def test_notifications_use_the_settings_from_the_database(logged_in_client, monkeypatch):
    """Konfigurace z administrace se musí dostat až do odeslání."""
    from app.core import mailer
    from app.core.db import async_session_factory
    from app.modules.notifications import service as notifications
    from app.models.core import User

    await _save(logged_in_client, smtp_password=SECRET, smtp_host="smtp.z-databaze.cz")

    used = {}

    async def fake_send(config, to_email, subject, body):
        used["host"] = config.host
        used["password"] = config.password
        used["sender"] = config.sender
        return None

    monkeypatch.setattr(mailer, "send_mail", fake_send)

    async with async_session_factory() as db:
        recipient = (await db.execute(select(User).order_by(User.created_at))).scalars().first()
        await notifications.create(
            db, user=recipient, vehicle=None, kind="deadline",
            title="Zkouška", body="tělo",
        )

    assert used["host"] == "smtp.z-databaze.cz"
    assert used["password"] == SECRET
    assert used["sender"] == "kniha-jizd@example.com"


async def test_unreadable_password_stops_sending_with_a_clear_message():
    """Radši srozumitelná hláška než nepovedené přihlášení k serveru."""
    from app.core import mailer

    broken = app_settings.SmtpConfig(
        host="smtp.example.com", port=587, user="a@b.cz",
        password="", sender="x@y.cz", password_unreadable=True,
    )
    error = await mailer.send_mail(broken, "kdo@example.com", "Předmět", "tělo")
    assert error is not None
    assert "nelze přečíst" in error


async def test_without_a_host_nothing_is_sent():
    from app.core import mailer

    error = await mailer.send_mail(
        app_settings.SmtpConfig(), "kdo@example.com", "Předmět", "tělo",
    )
    assert error == "SMTP není nakonfigurováno"


# --- zkušební e-mail ----------------------------------------------------

async def test_test_mail_goes_to_the_logged_in_admin_only(logged_in_client, admin_user, monkeypatch):
    """Adresa se nebere z formuláře - jinak by šlo přes administraci
    rozesílat poštu komukoliv."""
    from app.core import mailer

    await _save(logged_in_client, smtp_password=SECRET)
    recorded = []

    async def fake_send(config, to_email, subject, body):
        recorded.append(to_email)
        return None

    monkeypatch.setattr(mailer, "send_mail", fake_send)

    page = await logged_in_client.get("/kniha-jizd/settings/mail")
    response = await logged_in_client.post(
        "/kniha-jizd/settings/mail/test",
        data={"csrf_token": extract_csrf_token(page.text), "to": "cizi@example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert recorded == [admin_user[0]]


async def test_failed_test_mail_reports_the_reason(logged_in_client, monkeypatch):
    from app.core import mailer

    await _save(logged_in_client, smtp_password=SECRET)

    async def failing(config, to_email, subject, body):
        return "Connection refused"

    monkeypatch.setattr(mailer, "send_mail", failing)

    page = await logged_in_client.get("/kniha-jizd/settings/mail")
    response = await logged_in_client.post(
        "/kniha-jizd/settings/mail/test",
        data={"csrf_token": extract_csrf_token(page.text)}, follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Connection refused" in response.text
