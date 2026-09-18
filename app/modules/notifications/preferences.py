"""Čtení a ukládání individuálních preferencí notifikací (zadání 20).

Rozhodnutí „poslat, nebo neposlat" se dělá **za každého příjemce zvlášť**
(`is_enabled`), ne jednou za celou událost. Když se změní odpovědná
osoba vozidla, nová automaticky nedědí nic po předchozí - platí její
vlastní volby, protože se ptáme jejího `user_id`, ne vozidla.

Chybějící řádek = výchozí hodnota z katalogu, ne „vypnuto". Viz
`app/core/notification_types.py` a ROZHODNUTI.md R38.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.notification_types import DEFAULTS, TYPES_BY_CODE, type_for_kind
from app.models.core import UserNotificationPreference


async def get_all(db: AsyncSession, user_id: uuid.UUID) -> dict[str, bool]:
    """Kompletní nastavení uživatele: výchozí hodnoty překryté tím, co si
    sám zvolil. Tohle je to, co vidí na obrazovce."""
    stored = await _stored(db, user_id)
    return {**DEFAULTS, **stored}


async def _stored(db: AsyncSession, user_id: uuid.UUID) -> dict[str, bool]:
    rows = (await db.execute(
        select(UserNotificationPreference.notification_type, UserNotificationPreference.enabled)
        .where(UserNotificationPreference.user_id == user_id)
    )).all()
    # Řádek typu, který už v katalogu není (typ se přejmenoval nebo
    # zrušil), se ignoruje - mazat ho není potřeba a nic neovlivní.
    return {code: enabled for code, enabled in rows if code in TYPES_BY_CODE}


async def is_enabled(db: AsyncSession, user_id: uuid.UUID, *, kind: str) -> bool:
    """Chce tenhle uživatel dostat zprávu tohoto druhu?

    Neznámý `kind` projde. Zpráva, kterou někdo zapomněl zaregistrovat v
    katalogu, se nesmí tiše ztratit - to by byla chyba, na kterou se
    přijde až tím, že někomu nedorazí něco důležitého."""
    notification_type = type_for_kind(kind)
    if notification_type is None:
        return True

    row = (await db.execute(
        select(UserNotificationPreference.enabled).where(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.notification_type == notification_type.code,
        )
    )).scalar_one_or_none()
    return notification_type.default_enabled if row is None else row


async def save(
    db: AsyncSession, user_id: uuid.UUID, values: dict[str, bool], *, commit: bool = True,
) -> dict[str, bool]:
    """Uloží volby jednoho uživatele. Cizích se to nedotkne.

    Zapisují se i volby shodné s výchozí hodnotou: uživatel, který si
    přepínač vědomě nechal zapnutý, tím nemá přijít o své rozhodnutí,
    kdyby se výchozí hodnota v katalogu někdy změnila."""
    for code, enabled in values.items():
        if code not in TYPES_BY_CODE:
            continue
        # ON CONFLICT proti unikátnímu klíči: dvě uložení ve stejnou
        # chvíli nesmí skončit dvěma protichůdnými řádky ani chybou.
        await db.execute(
            insert(UserNotificationPreference)
            .values(user_id=user_id, notification_type=code, enabled=bool(enabled))
            .on_conflict_do_update(
                constraint="uq_core_user_notification_pref",
                # updated_at výslovně: onupdate z modelu se u ON CONFLICT
                # neuplatní, protože tohle není ORM update.
                set_={"enabled": bool(enabled), "updated_at": func.now()},
            )
        )
    await db.flush()
    if commit:
        await db.commit()
    return await get_all(db, user_id)
