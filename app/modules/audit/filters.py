"""Filtr auditní obrazovky (zadání 26/30).

Stejný vzor jako u knihy jízd (`logbook/filters.py`): čistá datová
třída, kterou parsuje `from_params` a spotřebovává jak výpis, tak
stránkování. Parsování je shovívavé - rozbitý parametr v URL se zahodí
a filtr se prostě nepoužije, místo 422.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Prague")

PAGE_SIZE = 50

#: Modul "auth" drží technické události přihlášení; všechno ostatní je
#: business audit. Rozlišuje se jím filtr „jen přihlášení".
AUTH_MODULE = "auth"

RESULTS = ("success", "failure")


def _parse_date(raw) -> date | None:
    text = str(raw or "").strip()
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _parse_uuid(raw) -> uuid.UUID | None:
    text = str(raw or "").strip()
    try:
        return uuid.UUID(text) if text else None
    except ValueError:
        return None


def _clean(raw, *, max_length: int = 120) -> str | None:
    text = str(raw or "").strip()
    return text[:max_length] or None


@dataclass(frozen=True)
class AuditFilter:
    date_from: date | None = None
    date_to: date | None = None
    user_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    module: str | None = None
    action: str | None = None
    result: str | None = None
    #: "logins" = jen přihlášení, "changes" = jen změny, None = obojí.
    event_kind: str | None = None
    search: str | None = None
    page: int = 1

    @classmethod
    def from_params(cls, params) -> "AuditFilter":
        date_from = _parse_date(params.get("from"))
        date_to = _parse_date(params.get("to"))
        if date_from and date_to and date_to < date_from:
            date_from, date_to = date_to, date_from

        raw_page = str(params.get("page") or "1").strip()
        page = int(raw_page) if raw_page.isdigit() and int(raw_page) > 0 else 1

        result = _clean(params.get("result"), max_length=20)
        event_kind = _clean(params.get("events"), max_length=10)

        return cls(
            date_from=date_from,
            date_to=date_to,
            user_id=_parse_uuid(params.get("user")),
            vehicle_id=_parse_uuid(params.get("vehicle")),
            module=_clean(params.get("module"), max_length=50),
            action=_clean(params.get("action"), max_length=50),
            result=result if result in RESULTS else None,
            event_kind=event_kind if event_kind in ("logins", "changes") else None,
            search=_clean(params.get("q"), max_length=200),
            page=page,
        )

    @property
    def created_from(self) -> datetime | None:
        if self.date_from is None:
            return None
        return datetime.combine(self.date_from, time.min, tzinfo=LOCAL_TZ)

    @property
    def created_to(self) -> datetime | None:
        """Půlnoc PO zvoleném dni - jinak by záznam z 17:00 posledního dne
        vypadl."""
        if self.date_to is None:
            return None
        return datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE

    @property
    def is_empty(self) -> bool:
        return not any((self.date_from, self.date_to, self.user_id, self.vehicle_id,
                        self.module, self.action, self.result, self.event_kind, self.search))

    def query_string(self, **overrides) -> str:
        values = {
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "user": str(self.user_id) if self.user_id else None,
            "vehicle": str(self.vehicle_id) if self.vehicle_id else None,
            "module": self.module,
            "action": self.action,
            "result": self.result,
            "events": self.event_kind,
            "q": self.search,
            "page": self.page if self.page > 1 else None,
        }
        values.update(overrides)
        return "&".join(f"{k}={v}" for k, v in values.items() if v not in (None, ""))
