"""Katalog typů notifikací a jejich výchozích hodnot (zadání 20).

Jedno místo, které odpovídá na tři otázky: **jaké typy existují**, **co
je u každého výchozí** a **který `Notification.kind` patří do kterého
typu**. Obrazovka s přepínači, kontrola před odesláním i testy čtou
odsud; nikde jinde není seznam typů napsaný podruhé.

Přidání nového typu je jeden záznam v `TYPES`. Obrazovka ho vypíše sama,
kontrola ho začne respektovat sama a uživatelé bez uloženého záznamu
dostanou jeho výchozí hodnotu — bez migrace a bez dávkového přepočtu
(viz `preferences.py` a ROZHODNUTI.md R38).

Typ a `kind` schválně nejsou totéž. `kind` je technický druh konkrétní
zprávy (`approval_request`, `approval_decision`), typ je to, co dává
smysl uživateli jako jeden přepínač („Schvalování vozidel"). Kdyby to
bylo jedna věc, přibyl by uživateli přepínač pokaždé, když v aplikaci
vznikne nová varianta zprávy.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationType:
    code: str
    label: str
    description: str
    #: Hodnota pro uživatele, který si nic nenastavil - tedy i pro
    #: každého nově založeného.
    default_enabled: bool
    #: Hodnoty Notification.kind, které pod tenhle přepínač spadají.
    kinds: tuple[str, ...]


# Pořadí je pořadí na obrazovce: napřed to, co se týká každého řidiče,
# pak správcovské věci.
TYPES: tuple[NotificationType, ...] = (
    NotificationType(
        code="reservation",
        label="Rezervace vozidel",
        description="Nová rezervace vozidla, které máte na starosti, nebo rezervace založená za vás.",
        default_enabled=True,
        kinds=("reservation",),
    ),
    NotificationType(
        code="reservation_change",
        label="Změny a zrušení rezervací",
        description="Někdo posunul nebo zrušil rezervaci, která se vás týká.",
        default_enabled=True,
        kinds=("reservation_change",),
    ),
    NotificationType(
        code="vehicle_deadlines",
        label="Připomínky termínů vozidel",
        description="Blížící se STK, konec platnosti pojištění, dálniční známky nebo servisní prohlídka.",
        default_enabled=True,
        kinds=("deadline",),
    ),
    NotificationType(
        code="approval",
        label="Schvalování vozidel",
        description="Žádost o vozidlo, které schvalujete, a výsledek vaší vlastní žádosti.",
        default_enabled=True,
        kinds=("approval_request", "approval_decision"),
    ),
    NotificationType(
        code="defect",
        label="Závady vozidel",
        description="Nově nahlášená závada na vašem vozidle a změna stavu závady, kterou jste nahlásili.",
        default_enabled=True,
        kinds=("defect",),
    ),
    NotificationType(
        code="trip",
        label="Výpůjčky vozidel",
        description="Zahájení a ukončení jízdy vozidlem, které máte na starosti.",
        default_enabled=True,
        kinds=("trip_start", "trip_end"),
    ),
)

TYPES_BY_CODE: dict[str, NotificationType] = {t.code: t for t in TYPES}

# kind -> kód typu. Staví se z TYPES, aby nešlo zapomenout na jedné ze
# dvou stran.
TYPE_CODE_BY_KIND: dict[str, str] = {
    kind: notification_type.code
    for notification_type in TYPES
    for kind in notification_type.kinds
}

DEFAULTS: dict[str, bool] = {t.code: t.default_enabled for t in TYPES}


def type_for_kind(kind: str) -> NotificationType | None:
    """Typ, pod který zpráva spadá, nebo `None` u neznámého druhu.

    `None` znamená „posílat" - viz `preferences.is_enabled`. Nová zpráva,
    kterou někdo zapomene zaregistrovat, se tak nesmí tiše ztratit."""
    code = TYPE_CODE_BY_KIND.get(kind)
    return TYPES_BY_CODE.get(code) if code else None
