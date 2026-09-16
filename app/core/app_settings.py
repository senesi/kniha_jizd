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
    SettingDef("oil_warn_km", "Výměna oleje – oranžová (km předem)", 1000,
               "Kolik km před dosažením intervalu se zobrazí oranžový semafor."),
    SettingDef("oil_warn_days", "Výměna oleje – oranžová (dní předem)", 30,
               "Kolik dní před dosažením časového intervalu se zobrazí oranžový semafor."),
    SettingDef("oil_notify_km", "Výměna oleje – upozornění (km předem)", 1000,
               "Kolik km před intervalem se odešle upozornění odpovědné osobě."),
    SettingDef("oil_notify_days", "Výměna oleje – upozornění (dní předem)", 30,
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
