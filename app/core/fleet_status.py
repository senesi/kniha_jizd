"""Semafor for a vehicle's mandatory deadlines (zadání 7/19).

Pure functions over a Vehicle plus the admin-configured thresholds - no
database access, no request context - so the same computation serves the
vehicle card, the dashboard, the list view and the notification job, and
can be unit-tested without a session.

Levels: "green" (comfortable margin), "orange" (deadline approaching),
"red" (passed / must be dealt with), "unknown" (no date on file - shown
neutrally rather than as a false green).
"""
from dataclasses import dataclass
from datetime import date

from app.models.fleet import Vehicle

LEVEL_ORDER = {"red": 0, "orange": 1, "unknown": 2, "green": 3}


@dataclass(frozen=True)
class DeadlineStatus:
    """One traffic light. `detail` is already a human sentence in Czech so
    templates never have to assemble one from the numbers."""

    code: str            # "stk" | "insurance" | "vignette" | "oil"
    label: str
    level: str           # green | orange | red | unknown
    due_date: date | None = None
    days_left: int | None = None
    km_left: int | None = None
    detail: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.level in ("orange", "red")


def _format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "-"


def _date_deadline(
    *, code: str, label: str, due: date | None, warn_days: int, today: date, missing_detail: str
) -> DeadlineStatus:
    if due is None:
        return DeadlineStatus(code=code, label=label, level="unknown", detail=missing_detail)
    days_left = (due - today).days
    if days_left < 0:
        level = "red"
        detail = f"Po termínu o {abs(days_left)} dní ({_format_date(due)})."
    elif days_left <= warn_days:
        level = "orange"
        detail = f"Zbývá {days_left} dní (do {_format_date(due)})."
    else:
        level = "green"
        detail = f"Platí do {_format_date(due)} ({days_left} dní)."
    return DeadlineStatus(code=code, label=label, level=level, due_date=due, days_left=days_left, detail=detail)


def stk_status(vehicle: Vehicle, settings: dict[str, int], today: date | None = None) -> DeadlineStatus:
    return _date_deadline(
        code="stk", label="STK", due=vehicle.stk_valid_until,
        warn_days=settings["stk_warn_days"], today=today or date.today(),
        missing_detail="Termín STK není zadán.",
    )


def vignette_status(vehicle: Vehicle, settings: dict[str, int], today: date | None = None) -> DeadlineStatus:
    return _date_deadline(
        code="vignette", label="Dálniční známka", due=vehicle.vignette_valid_until,
        warn_days=settings["vignette_warn_days"], today=today or date.today(),
        missing_detail="Platnost dálniční známky není zadána.",
    )


def insurance_status(vehicle: Vehicle, settings: dict[str, int], today: date | None = None) -> DeadlineStatus:
    return _date_deadline(
        code="insurance", label="Pojištění", due=vehicle.insurance_valid_until,
        warn_days=settings["insurance_warn_days"], today=today or date.today(),
        missing_detail="Platnost pojištění není zadána.",
    )


def _add_months(value: date, months: int) -> date:
    """Calendar-month arithmetic without a dateutil dependency. Clamps to
    the last valid day (31.1. + 1 month -> 28./29.2.)."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = value.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def oil_status(vehicle: Vehicle, settings: dict[str, int], today: date | None = None) -> DeadlineStatus:
    """Next oil change, by km interval and/or month interval - whichever
    comes first wins, exactly as a service book would read it. Computed on
    every read from last_oil_change_* plus the intervals; never stored, so
    it can never go stale after an odometer update."""
    today = today or date.today()
    # „Servisní prohlídka", ne „výměna oleje": interval platí i pro
    # elektromobil, který olej nemá, ale prohlídku a náplně ano.
    label = "Servisní prohlídka"
    has_km_rule = vehicle.oil_interval_km and vehicle.last_oil_change_km is not None
    has_time_rule = vehicle.oil_interval_months and vehicle.last_oil_change_at is not None
    if not has_km_rule and not has_time_rule:
        return DeadlineStatus(
            code="oil", label=label, level="unknown",
            detail="Interval výměny oleje není nastaven.",
        )

    candidates: list[DeadlineStatus] = []

    if has_km_rule:
        due_km = vehicle.last_oil_change_km + vehicle.oil_interval_km
        km_left = due_km - vehicle.current_odometer_km
        if km_left < 0:
            level, detail = "red", f"Po termínu o {abs(km_left)} km (mělo být při {due_km:,} km)."
        elif km_left <= settings["oil_warn_km"]:
            level, detail = "orange", f"Zbývá {km_left:,} km (výměna při {due_km:,} km)."
        else:
            level, detail = "green", f"Výměna při {due_km:,} km (zbývá {km_left:,} km)."
        candidates.append(DeadlineStatus(
            code="oil", label=label, level=level, km_left=km_left, detail=detail.replace(",", " "),
        ))

    if has_time_rule:
        due_date = _add_months(vehicle.last_oil_change_at, vehicle.oil_interval_months)
        days_left = (due_date - today).days
        if days_left < 0:
            level, detail = "red", f"Po termínu o {abs(days_left)} dní (mělo být {_format_date(due_date)})."
        elif days_left <= settings["oil_warn_days"]:
            level, detail = "orange", f"Zbývá {days_left} dní (do {_format_date(due_date)})."
        else:
            level, detail = "green", f"Výměna do {_format_date(due_date)} ({days_left} dní)."
        candidates.append(DeadlineStatus(
            code="oil", label=label, level=level, due_date=due_date, days_left=days_left, detail=detail,
        ))

    # The stricter of the two rules is the one that actually applies.
    return min(candidates, key=lambda s: LEVEL_ORDER[s.level])


def vehicle_deadlines(vehicle: Vehicle, settings: dict[str, int], today: date | None = None) -> list[DeadlineStatus]:
    """The deadlines shown prominently on arrival at the vehicle - the
    three from zadání 7 plus pojištění, which the fleet owner asked to
    have watched the same way (expired povinné ručení is a worse problem
    to discover on the road than an expired vignette)."""
    return [
        stk_status(vehicle, settings, today),
        insurance_status(vehicle, settings, today),
        vignette_status(vehicle, settings, today),
        oil_status(vehicle, settings, today),
    ]


def worst_level(statuses: list[DeadlineStatus]) -> str:
    if not statuses:
        return "unknown"
    return min((s.level for s in statuses), key=lambda level: LEVEL_ORDER[level])
