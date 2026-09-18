"""Sestavení týdenního kalendáře (zadání 9).

Čisté funkce nad už načtenými daty - žádná databáze, žádný request -
takže se dají testovat přímo a kalendář se chová stejně na mobilu,
tabletu i na PC.

Zadání chce, aby byl **volný termín na první pohled zřejmý**. Mřížka je
proto postavená obráceně, než by se čekalo: výchozí stav buňky je
„volno" a teprve rezervace, servis nebo probíhající jízda ji přebarví.
Prázdno tedy nikdy neznamená „nevíme", ale „je volno".
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Provozní časové pásmo. Data v databázi jsou v UTC (timestamptz); den v
# kalendáři ale musí začínat tam, kde ho začíná řidič.
LOCAL_TZ = ZoneInfo("Europe/Prague")

DAY_NAMES = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

# Pořadí důležitosti, když na jeden den padne víc věcí. Probíhající jízda
# je fakt, rezervace jen nárok - proto je výš.
LEVEL_PRIORITY = {"trip": 0, "service": 1, "reserved": 2, "free": 3}


@dataclass
class DayCell:
    day: date
    level: str = "free"
    labels: list[str] = field(default_factory=list)
    is_today: bool = False
    is_past: bool = False

    def add(self, level: str, label: str) -> None:
        self.labels.append(label)
        if LEVEL_PRIORITY[level] < LEVEL_PRIORITY[self.level]:
            self.level = level

    @property
    def is_free(self) -> bool:
        return self.level == "free"

    @property
    def tooltip(self) -> str:
        return " • ".join(self.labels) if self.labels else "Volné"


@dataclass
class VehicleRow:
    vehicle: object
    cells: list[DayCell]

    @property
    def free_days(self) -> int:
        return sum(1 for cell in self.cells if cell.is_free and not cell.is_past)


def week_start(anchor: date) -> date:
    """Pondělí týdne, do kterého datum spadá."""
    return anchor - timedelta(days=anchor.weekday())


def week_days(start: date, count: int = 7) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def window_bounds(days: list[date]) -> tuple[datetime, datetime]:
    """Časové okno celého zobrazeného rozsahu, v UTC - přesně to, na co se
    ptá repozitář."""
    first = datetime.combine(days[0], time.min, tzinfo=LOCAL_TZ)
    last = datetime.combine(days[-1] + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    return first, last


def _day_end(day: date) -> datetime:
    """Půlnoc následujícího dne - horní mez dne, ne poslední okamžik."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)


def _overlaps_day(start_at: datetime, end_at: datetime | None, day: date) -> bool:
    day_start = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    day_end = day_start + timedelta(days=1)
    if end_at is None:
        # Záznam bez konce obsazuje všechno od svého začátku dál. Pro
        # probíhající jízdy sem volající konec dosazuje (viz build_rows),
        # takže tahle větev zbývá na rezervace bez konce.
        return start_at < day_end
    return start_at < day_end and end_at > day_start


def _time_label(start_at: datetime, end_at: datetime | None, day: date) -> str:
    """„08:00–16:30" pro jednodenní záznam, „celý den" pro ten, co daným
    dnem jen prochází. Formát %H (ne %-H) - %-H na Windows padá a testy
    běží právě tam."""
    local_start = start_at.astimezone(LOCAL_TZ)
    local_end = end_at.astimezone(LOCAL_TZ) if end_at else None
    starts_today = local_start.date() == day
    ends_today = local_end is not None and local_end.date() == day

    if starts_today and ends_today:
        return f"{local_start:%H:%M}–{local_end:%H:%M}"
    if starts_today:
        return f"od {local_start:%H:%M}"
    if ends_today:
        return f"do {local_end:%H:%M}"
    return "celý den"


def build_rows(vehicles, reservations, active_trips, days: list[date], today: date | None = None) -> list[VehicleRow]:
    """Mřížka vozidlo × den. `vehicles` určuje pořadí řádků, takže i
    vozidlo bez jediné rezervace má svůj řádek - jinak by nebylo vidět,
    že je celý týden volné."""
    today = today or datetime.now(LOCAL_TZ).date()
    # Probíhající jízda nemá konec, ale brát ji doslova by znamenalo
    # obarvit „jede" všechny zbývající dny v týdnu a vozidlo by pak nešlo
    # rezervovat na žádný z nich. To, že se řidič dneska ještě nevrátil,
    # ale o pátku nic neříká - dnešek je poslední den, o kterém něco víme.
    # Dál už je vozidlo volné a rezervovatelné; jakmile jízda skutečně
    # přeteče do dalšího dne, obarví se ten den sám.
    end_of_today = _day_end(today)

    rows: dict = {}
    for vehicle in vehicles:
        rows[vehicle.id] = VehicleRow(
            vehicle=vehicle,
            cells=[
                DayCell(day=day, is_today=(day == today), is_past=(day < today))
                for day in days
            ],
        )

    for reservation in reservations:
        row = rows.get(reservation.vehicle_id)
        if row is None:
            continue
        level = "service" if reservation.kind == "service" else "reserved"
        who = reservation.user.full_name if reservation.user else "Servis / mimo provoz"
        for cell in row.cells:
            if _overlaps_day(reservation.start_at, reservation.end_at, cell.day):
                cell.add(level, f"{who} ({_time_label(reservation.start_at, reservation.end_at, cell.day)})")

    for trip in active_trips:
        row = rows.get(trip.vehicle_id)
        if row is None:
            continue
        # Otevřená jízda končí koncem dneška - nebo koncem dne, kdy
        # začala, kdyby snad začínala později (ručně opravený čas startu).
        # Den, kdy se vyjelo, musí být v kalendáři vidět vždycky.
        started_day = trip.started_at.astimezone(LOCAL_TZ).date()
        open_end = max(end_of_today, _day_end(started_day))
        ends_at = trip.ended_at if trip.ended_at is not None else open_end
        for cell in row.cells:
            if _overlaps_day(trip.started_at, ends_at, cell.day):
                cell.add("trip", f"Právě jede: {trip.primary_driver.full_name}")

    return [rows[vehicle.id] for vehicle in vehicles]
