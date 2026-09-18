"""Append-only auditní log (zadání 26/30).

Payload se před zápisem vždy projde `sanitize()`. Sloupce `before_data`
a `after_data` jsou JSON, a do JSONu neprojde `Decimal` (Numeric sloupce
jako objem nádrže nebo cena), `date`, `datetime`, `UUID` ani `set`.

Dřív si každý modul převáděl hodnoty sám a stačilo jednou zapomenout:
úprava vozidla s vyplněným objemem nádrže padala na
`TypeError: Object of type Decimal is not JSON serializable` - tedy 500
při běžném uložení formuláře. Konverze proto patří sem, do jediného
místa, kterým každý zápis prochází, ne do volajících.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditLog


def sanitize(value):
    """Cokoliv na strukturu, kterou `json.dumps` spolkne.

    Decimal jde na float záměrně: audit je záznam o tom, co se stalo, ne
    účetní doklad, a float je v JSONu čitelný. Neznámý typ skončí jako
    `str()` - radši méně přesný záznam než ztracený."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # Množiny nemají pořadí - srovnat, aby byl záznam reprodukovatelný.
        return sorted(sanitize(item) for item in value)
    return str(value)


# Klíče, které se do auditu nesmí dostat, ani kdyby je volající omylem
# poslal v payloadu. Audit je určený ke čtení administrátorem a jeho
# obsah putuje do záloh - tajemství v něm nemá co dělat (zadání 30).
FORBIDDEN_KEYS = frozenset({
    "password", "password_hash", "new_password", "current_password", "heslo",
    "smtp_password", "session", "session_token", "token", "csrf_token",
    "secret", "secret_key", "session_secret_key", "api_key", "authorization",
})

REDACTED = "[odstraněno]"


def scrub(payload: dict | None) -> dict | None:
    """Vyhodí z payloadu tajemství.

    Druhá pojistka za tím, že je volající neposílá. Volajících jsou
    desítky, budou přibývat, a heslo zapsané do auditu se zpětně
    neodstraní - je v zálohách. Klíč se porovnává bez ohledu na velikost
    písmen a hodnota se nahradí, ne smaže: ze záznamu má být poznat, že
    se to pole měnilo."""
    if not payload:
        return payload
    cleaned = {}
    for key, value in payload.items():
        if str(key).lower() in FORBIDDEN_KEYS:
            cleaned[key] = REDACTED
        elif isinstance(value, dict):
            cleaned[key] = scrub(value)
        else:
            cleaned[key] = value
    return cleaned


def _vehicle_from_payload(*payloads) -> uuid.UUID | None:
    """Vozidlo z payloadu, když ho volající nepředal zvlášť.

    Většina modulů `vehicle_id` do payloadu dávala dávno předtím, než měl
    audit vlastní sloupec. Tímhle je nemusím všechny obcházet a filtr
    podle vozidla funguje i u nich."""
    for payload in payloads:
        if not payload:
            continue
        raw = payload.get("vehicle_id")
        if not raw:
            continue
        try:
            return uuid.UUID(str(raw))
        except (ValueError, AttributeError):
            continue
    return None


async def log_action(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    module: str,
    entity_type: str,
    entity_id: str | None = None,
    vehicle_id: uuid.UUID | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    description: str | None = None,
    result: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append-only auditní záznam. Commit dělá volající."""
    before_data = scrub(before_data)
    after_data = scrub(after_data)
    if vehicle_id is None:
        # Když je auditovaným objektem samo vozidlo, je jeho id rovnou
        # entity_id - do payloadu ho nikdo neduplikuje.
        if entity_type == "vehicle" and entity_id:
            try:
                vehicle_id = uuid.UUID(str(entity_id))
            except ValueError:
                vehicle_id = None
        if vehicle_id is None:
            vehicle_id = _vehicle_from_payload(after_data, before_data)

    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            module=module,
            entity_type=entity_type,
            entity_id=entity_id,
            vehicle_id=vehicle_id,
            before_data=sanitize(before_data) if before_data is not None else None,
            after_data=sanitize(after_data) if after_data is not None else None,
            description=(description or None),
            result=result,
            ip_address=ip_address,
            # Dlouhý user-agent se ořízne, ať zápis neshodí délka sloupce.
            user_agent=(user_agent or "")[:255] or None,
        )
    )
