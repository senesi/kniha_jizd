"""Admin-configurable thresholds (zadání 7/19/20: "Prahové hodnoty musí
být konfigurovatelné administrátorem").

Stored as plain key/value rows in core.app_settings so adding a threshold
never needs a migration. Every key has a default here, so a fresh database
- or one where an admin has never opened the settings page - behaves
sensibly without any rows at all.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AppSetting


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    default: int
    help_text: str = ""


# Traffic light (zadání 7): green above the "warn" threshold, orange from
# there down to the deadline, red once it has passed. Two numbers per
# deadline - the orange threshold and how far ahead the notification goes
# out - because "show me a warning" and "mail the responsible person" are
# genuinely different moments.
SETTING_DEFS: list[SettingDef] = [
    SettingDef("stk_warn_days", "STK – oranžová (dní předem)", 30,
               "Od kolika dní před koncem platnosti STK se zobrazí oranžový semafor."),
    SettingDef("stk_notify_days", "STK – upozornění e-mailem (dní předem)", 30,
               "Kolik dní před koncem platnosti STK se odešle upozornění odpovědné osobě."),
    SettingDef("insurance_warn_days", "Pojištění – oranžová (dní předem)", 30,
               "Od kolika dní před koncem platnosti pojištění se zobrazí oranžový semafor."),
    SettingDef("insurance_notify_days", "Pojištění – upozornění e-mailem (dní předem)", 30,
               "Kolik dní před koncem platnosti pojištění se odešle upozornění odpovědné osobě."),
    SettingDef("vignette_warn_days", "Dálniční známka – oranžová (dní předem)", 14,
               "Od kolika dní před koncem platnosti známky se zobrazí oranžový semafor."),
    SettingDef("vignette_notify_days", "Dálniční známka – upozornění e-mailem (dní předem)", 14,
               "Kolik dní před koncem platnosti známky se odešle upozornění."),
    # Klíče zůstávají "oil_*" - jsou v databázi a přejmenování by si
    # vyžádalo migraci bez jakéhokoliv přínosu. Mění se jen popisky.
    SettingDef("oil_warn_km", "Servisní prohlídka – oranžová (km předem)", 1000,
               "Kolik km před dosažením intervalu se zobrazí oranžový semafor."),
    SettingDef("oil_warn_days", "Servisní prohlídka – oranžová (dní předem)", 30,
               "Kolik dní před dosažením časového intervalu se zobrazí oranžový semafor."),
    SettingDef("oil_notify_km", "Servisní prohlídka – upozornění (km předem)", 1000,
               "Kolik km před intervalem se odešle upozornění odpovědné osobě."),
    SettingDef("oil_notify_days", "Servisní prohlídka – upozornění (dní předem)", 30,
               "Kolik dní před časovým intervalem se odešle upozornění."),
    SettingDef("distance_tolerance_percent", "Kontrola trasy – tolerance (%)", 20,
               "O kolik procent se smí ujetá vzdálenost lišit od mapového odhadu, než se zobrazí upozornění."),
    SettingDef("odometer_jump_warn_km", "Podezřelý skok tachometru (km)", 2000,
               "Nad kolik km v jedné jízdě se vyžádá potvrzení, že hodnota je správná."),
    SettingDef("fueling_max_liters", "Maximální množství paliva na jedno tankování (l)", 300,
               "Nad tuto hodnotu se tankování odmítne jako zjevně chybné."),
    SettingDef("charging_max_kwh", "Maximální množství energie na jedno nabití (kWh)", 250,
               "Nad tuto hodnotu se nabíjení elektromobilu odmítne jako zjevně chybné."),
]

DEFAULTS: dict[str, int] = {d.key: d.default for d in SETTING_DEFS}


async def get_all(db: AsyncSession) -> dict[str, int]:
    """Every threshold, defaults filled in for keys with no row yet."""
    result = await db.execute(select(AppSetting))
    stored = {}
    for row in result.scalars().all():
        try:
            stored[row.key] = int(row.value)
        except (TypeError, ValueError):
            # A non-numeric value can only come from a manual DB edit -
            # fall back to the default rather than 500-ing a page that
            # merely wanted to render a traffic light.
            continue
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


async def get_value(db: AsyncSession, key: str) -> int:
    settings = await get_all(db)
    return settings[key]


async def set_values(db: AsyncSession, values: dict[str, int], actor_id: uuid.UUID) -> None:
    """Upserts the given keys. Unknown keys are ignored - the settings form
    posts exactly SETTING_DEFS, and anything else is not a setting this app
    knows how to use. Caller commits."""
    result = await db.execute(select(AppSetting))
    existing = {row.key: row for row in result.scalars().all()}
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        row = existing.get(key)
        if row is None:
            db.add(AppSetting(key=key, value=str(value), updated_by=actor_id))
        else:
            row.value = str(value)
            row.updated_by = actor_id
            row.updated_at = datetime.now(timezone.utc)


# ======================================================================
# SMTP - nastavitelné administrátorem (dřív jen v .env)
# ======================================================================
#
# Leží ve stejné tabulce jako prahy, jen pod vlastními klíči. `get_all`
# i `set_values` cizí klíče ignorují, takže se obě skupiny nepletou a
# nevzniklo druhé úložiště nastavení.
#
# **Hodnota z administrace má přednost, .env zůstává záložní.** Po poli,
# ne po celé skupině: kdo vyplní jen server a přihlášení, nepřijde tím o
# adresu odesílatele nastavenou v .env. Nevyplněné pole se tedy chová
# jako „tohle neřeším", ne jako „smazat".

SMTP_HOST_KEY = "smtp_host"
SMTP_PORT_KEY = "smtp_port"
SMTP_USER_KEY = "smtp_user"
SMTP_PASSWORD_KEY = "smtp_password"
SMTP_FROM_KEY = "smtp_from"
SMTP_STARTTLS_KEY = "smtp_starttls"

SMTP_KEYS = (
    SMTP_HOST_KEY, SMTP_PORT_KEY, SMTP_USER_KEY,
    SMTP_PASSWORD_KEY, SMTP_FROM_KEY, SMTP_STARTTLS_KEY,
)


@dataclass(frozen=True)
class SmtpConfig:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = ""
    starttls: bool = True
    #: Odkud se vzaly hodnoty - pro nápovědu na obrazovce nastavení.
    from_database: bool = False
    #: True, když je v databázi heslo, které se nepodařilo rozšifrovat
    #: (typicky po výměně SESSION_SECRET_KEY).
    password_unreadable: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


async def _raw_rows(db: AsyncSession, keys) -> dict[str, str]:
    result = await db.execute(select(AppSetting).where(AppSetting.key.in_(list(keys))))
    return {row.key: row.value for row in result.scalars().all()}


async def get_smtp(db: AsyncSession) -> SmtpConfig:
    """Nastavení pošty: co je v databázi, doplněné tím, co je v .env."""
    from app.core.config import get_settings
    from app.core.crypto import decrypt_secret

    env = get_settings()
    stored = await _raw_rows(db, SMTP_KEYS)

    def text(key: str, fallback: str) -> str:
        value = (stored.get(key) or "").strip()
        return value or fallback

    port_raw = (stored.get(SMTP_PORT_KEY) or "").strip()
    try:
        port = int(port_raw) if port_raw else env.smtp_port
    except ValueError:
        port = env.smtp_port

    starttls_raw = (stored.get(SMTP_STARTTLS_KEY) or "").strip()
    starttls = env.smtp_starttls if starttls_raw == "" else starttls_raw == "1"

    password = env.smtp_password
    unreadable = False
    if (stored.get(SMTP_PASSWORD_KEY) or "").strip():
        decrypted = decrypt_secret(stored[SMTP_PASSWORD_KEY])
        if decrypted is None:
            unreadable = True
        else:
            password = decrypted

    return SmtpConfig(
        host=text(SMTP_HOST_KEY, env.smtp_host),
        port=port,
        user=text(SMTP_USER_KEY, env.smtp_user),
        password=password,
        sender=text(SMTP_FROM_KEY, env.smtp_from),
        starttls=starttls,
        from_database=bool((stored.get(SMTP_HOST_KEY) or "").strip()),
        password_unreadable=unreadable,
    )


async def set_smtp(
    db: AsyncSession, *, host: str, port: str, user: str, password: str | None,
    sender: str, starttls: bool, actor_id: uuid.UUID,
) -> None:
    """Uloží nastavení pošty. Volající commituje.

    `password=None` znamená „nech, co tam je" - formulář heslo nikdy
    nevypisuje zpátky, takže prázdné pole nesmí uložené heslo smazat.
    Smazat ho jde vyprázdněním serveru, tedy vypnutím odesílání."""
    from app.core.crypto import encrypt_secret

    values = {
        SMTP_HOST_KEY: host.strip(),
        SMTP_PORT_KEY: str(port).strip(),
        SMTP_USER_KEY: user.strip(),
        SMTP_FROM_KEY: sender.strip(),
        SMTP_STARTTLS_KEY: "1" if starttls else "0",
    }
    if password is not None:
        # Do databáze nikdy v čitelné podobě - skončila by i v zálohách
        # (app/core/crypto.py).
        values[SMTP_PASSWORD_KEY] = encrypt_secret(password)

    existing = {row.key: row for row in (
        await db.execute(select(AppSetting).where(AppSetting.key.in_(list(SMTP_KEYS))))
    ).scalars().all()}

    for key, value in values.items():
        row = existing.get(key)
        if row is None:
            db.add(AppSetting(key=key, value=value, updated_by=actor_id))
        else:
            row.value = value
            row.updated_by = actor_id
            row.updated_at = datetime.now(timezone.utc)


async def has_stored_smtp_password(db: AsyncSession) -> bool:
    """Je v databázi uložené heslo? Obrazovka to potřebuje vědět, aniž by
    ho kdy dostala do ruky."""
    stored = await _raw_rows(db, (SMTP_PASSWORD_KEY,))
    return bool((stored.get(SMTP_PASSWORD_KEY) or "").strip())
