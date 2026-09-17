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


async def log_action(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    module: str,
    entity_type: str,
    entity_id: str | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append-only auditní záznam. Commit dělá volající."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            module=module,
            entity_type=entity_type,
            entity_id=entity_id,
            before_data=sanitize(before_data) if before_data is not None else None,
            after_data=sanitize(after_data) if after_data is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
