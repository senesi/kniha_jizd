"""Filtr knihy jízd (zadání 22).

Čistá datová třída bez databáze a bez requestu. Důvod je praktický:
**stejný filtr musí platit pro výpis i pro export** (zadání 23 - „export
musí respektovat aktivní filtry"). Kdyby si obrazovka skládala podmínky
sama a export znovu, dřív nebo později se rozejdou a uživatel dostane
do XLSX jiná data, než vidí na obrazovce.

Parsování je schválně shovívavé: nesmyslné datum nebo neznámý kód se
zahodí, místo aby stránka spadla na 422. Kniha jízd se otevírá z odkazů
a záložek a rozbitý parametr v URL nemá být konec světa - filtr se
prostě nepoužije a uživatel vidí, co je v něm nastavené.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.fleet import TRIP_PURPOSES, TRIP_STATUSES

# Stejné pásmo jako kalendář: databáze drží UTC, ale „od 1. 9." znamená
# od půlnoci v Praze, ne v UTC.
LOCAL_TZ = ZoneInfo("Europe/Prague")

# Strop na jednu stránku výpisu. Export tímhle omezený není - tam jde o
# celé období a limit by tiše ořízl data, což je horší než pomalejší
# stažení.
PAGE_SIZE = 50


def _parse_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_uuid(raw) -> uuid.UUID | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def _parse_choice(raw, allowed) -> str | None:
    text = str(raw or "").strip()
    return text if text in allowed else None


@dataclass(frozen=True)
class LogbookFilter:
    date_from: date | None = None
    date_to: date | None = None
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    purpose_code: str | None = None
    status: str | None = None
    page: int = 1

    @classmethod
    def from_params(cls, params) -> "LogbookFilter":
        """`params` je cokoliv s `.get()` - typicky request.query_params."""
        date_from = _parse_date(params.get("from"))
        date_to = _parse_date(params.get("to"))
        # Obrácené období není chyba uživatele, kterou má řešit hláškou -
        # očividně to myslel obráceně, tak se meze prohodí.
        if date_from and date_to and date_to < date_from:
            date_from, date_to = date_to, date_from

        raw_page = str(params.get("page") or "1").strip()
        page = int(raw_page) if raw_page.isdigit() and int(raw_page) > 0 else 1

        return cls(
            date_from=date_from,
            date_to=date_to,
            vehicle_id=_parse_uuid(params.get("vehicle")),
            driver_id=_parse_uuid(params.get("driver")),
            purpose_code=_parse_choice(params.get("purpose"), TRIP_PURPOSES),
            status=_parse_choice(params.get("status"), TRIP_STATUSES),
            page=page,
        )

    # --- hranice období v UTC -----------------------------------------

    @property
    def started_from(self) -> datetime | None:
        if self.date_from is None:
            return None
        return datetime.combine(self.date_from, time.min, tzinfo=LOCAL_TZ)

    @property
    def started_to(self) -> datetime | None:
        """Horní mez je půlnoc PO zvoleném dni - jinak by jízda zahájená
        v 17:00 posledního dne období vypadla."""
        if self.date_to is None:
            return None
        return datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)

    # --- pomůcky pro šablonu ------------------------------------------

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE

    @property
    def is_empty(self) -> bool:
        """Nic nevybráno - používá se jen pro hlášku „zobrazuje se vše"."""
        return not any((self.date_from, self.date_to, self.vehicle_id,
                        self.driver_id, self.purpose_code, self.status))

    def query_string(self, **overrides) -> str:
        """URL parametry filtru, volitelně s přepsanou hodnotou.

        Používá stránkování a odkazy na export, aby se filtr nemusel
        skládat na třech místech v šabloně."""
        values = {
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "vehicle": str(self.vehicle_id) if self.vehicle_id else None,
            "driver": str(self.driver_id) if self.driver_id else None,
            "purpose": self.purpose_code,
            "status": self.status,
            "page": self.page if self.page > 1 else None,
        }
        values.update(overrides)
        parts = [f"{key}={value}" for key, value in values.items() if value not in (None, "")]
        return "&".join(parts)
