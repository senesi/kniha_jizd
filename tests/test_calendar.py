"""Mřížka kalendáře - čisté funkce, bez databáze (zadání 9)."""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.modules.reservations.calendar import (
    LOCAL_TZ,
    build_rows,
    week_days,
    week_start,
    window_bounds,
)

MONDAY = date(2026, 9, 14)
DAYS = week_days(MONDAY)


@dataclass
class FakeUser:
    full_name: str


@dataclass
class FakeVehicle:
    id: uuid.UUID
    license_plate: str = "1AB 2345"
    brand: str = "Škoda"
    model: str = "Octavia"


@dataclass
class FakeReservation:
    vehicle_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    kind: str = "reservation"
    user: FakeUser | None = None


@dataclass
class FakeTrip:
    vehicle_id: uuid.UUID
    started_at: datetime
    primary_driver: FakeUser
    ended_at: datetime | None = None


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=LOCAL_TZ)


def _vehicle() -> FakeVehicle:
    return FakeVehicle(id=uuid.uuid4())


# --- základ ------------------------------------------------------------

def test_week_start_is_monday():
    assert week_start(date(2026, 9, 16)) == MONDAY   # ze středy
    assert week_start(MONDAY) == MONDAY              # z pondělí
    assert week_start(date(2026, 9, 20)) == MONDAY   # z neděle


def test_window_covers_whole_last_day():
    start, end = window_bounds(DAYS)
    assert start == _at(MONDAY, 0)
    # Konec je půlnoc PO neděli - jinak by rezervace v neděli večer vypadla.
    assert end == _at(MONDAY + timedelta(days=7), 0)


def test_empty_week_is_all_free():
    vehicle = _vehicle()
    rows = build_rows([vehicle], [], [], DAYS, today=MONDAY)
    assert len(rows) == 1
    assert all(cell.is_free for cell in rows[0].cells)
    assert rows[0].free_days == 7
    assert rows[0].cells[0].tooltip == "Volné"


def test_vehicle_without_reservations_still_has_a_row():
    """Bez řádku by nebylo poznat, že je vozidlo celý týden volné."""
    used, unused = _vehicle(), _vehicle()
    reservation = FakeReservation(
        vehicle_id=used.id, start_at=_at(DAYS[0], 8), end_at=_at(DAYS[0], 16), user=FakeUser("Petr"),
    )
    rows = build_rows([used, unused], [reservation], [], DAYS, today=MONDAY)
    assert len(rows) == 2
    assert rows[1].free_days == 7


# --- obsazení ----------------------------------------------------------

def test_single_day_reservation_marks_only_that_day():
    vehicle = _vehicle()
    reservation = FakeReservation(
        vehicle_id=vehicle.id, start_at=_at(DAYS[1], 8), end_at=_at(DAYS[1], 16), user=FakeUser("Petr Novák"),
    )
    cells = build_rows([vehicle], [reservation], [], DAYS, today=MONDAY)[0].cells

    assert cells[0].is_free
    assert cells[1].level == "reserved"
    assert "Petr Novák" in cells[1].tooltip
    assert "08:00–16:00" in cells[1].tooltip
    assert cells[2].is_free


def test_multi_day_reservation_marks_every_day_it_touches():
    vehicle = _vehicle()
    reservation = FakeReservation(
        vehicle_id=vehicle.id, start_at=_at(DAYS[1], 14), end_at=_at(DAYS[3], 10), user=FakeUser("Jana"),
    )
    cells = build_rows([vehicle], [reservation], [], DAYS, today=MONDAY)[0].cells

    assert cells[0].is_free
    assert [cell.level for cell in cells[1:4]] == ["reserved"] * 3
    assert cells[4].is_free
    # Prostřední den rezervací jen prochází.
    assert "od 14:00" in cells[1].tooltip
    assert cells[2].tooltip.endswith("(celý den)")
    assert "do 10:00" in cells[3].tooltip


def test_reservation_ending_at_midnight_does_not_bleed_into_next_day():
    vehicle = _vehicle()
    reservation = FakeReservation(
        vehicle_id=vehicle.id, start_at=_at(DAYS[0], 8), end_at=_at(DAYS[1], 0), user=FakeUser("Petr"),
    )
    cells = build_rows([vehicle], [reservation], [], DAYS, today=MONDAY)[0].cells
    assert cells[0].level == "reserved"
    assert cells[1].is_free


def test_service_block_is_distinguished_from_reservation():
    vehicle = _vehicle()
    block = FakeReservation(
        vehicle_id=vehicle.id, start_at=_at(DAYS[2], 0), end_at=_at(DAYS[4], 0), kind="service", user=None,
    )
    cells = build_rows([vehicle], [block], [], DAYS, today=MONDAY)[0].cells
    assert cells[2].level == "service"
    assert "Servis / mimo provoz" in cells[2].tooltip


def test_open_trip_occupies_every_following_day():
    """Probíhající jízda nemá konec - blokuje od svého začátku dál."""
    vehicle = _vehicle()
    trip = FakeTrip(vehicle_id=vehicle.id, started_at=_at(DAYS[2], 9), primary_driver=FakeUser("Karel"))
    cells = build_rows([vehicle], [], [trip], DAYS, today=MONDAY)[0].cells

    assert cells[1].is_free
    assert all(cell.level == "trip" for cell in cells[2:])
    assert "Karel" in cells[2].tooltip


def test_running_trip_outranks_reservation_on_the_same_day():
    """Rezervace je nárok, probíhající jízda fakt - vyhrává jízda."""
    vehicle = _vehicle()
    reservation = FakeReservation(
        vehicle_id=vehicle.id, start_at=_at(DAYS[2], 8), end_at=_at(DAYS[2], 16), user=FakeUser("Petr"),
    )
    trip = FakeTrip(vehicle_id=vehicle.id, started_at=_at(DAYS[2], 9), primary_driver=FakeUser("Karel"))
    cells = build_rows([vehicle], [reservation], [trip], DAYS, today=MONDAY)[0].cells

    assert cells[2].level == "trip"
    # Obojí zůstává v popisku - uživatel má vidět, že tam rezervace je.
    assert "Petr" in cells[2].tooltip
    assert "Karel" in cells[2].tooltip


def test_reservation_for_another_vehicle_is_ignored():
    mine, other = _vehicle(), _vehicle()
    reservation = FakeReservation(
        vehicle_id=other.id, start_at=_at(DAYS[1], 8), end_at=_at(DAYS[1], 16), user=FakeUser("Petr"),
    )
    rows = build_rows([mine], [reservation], [], DAYS, today=MONDAY)
    assert all(cell.is_free for cell in rows[0].cells)


# --- dnešek a minulost --------------------------------------------------

def test_today_and_past_are_flagged():
    vehicle = _vehicle()
    row = build_rows([vehicle], [], [], DAYS, today=DAYS[2])[0]

    assert [cell.is_past for cell in row.cells] == [True, True, False, False, False, False, False]
    assert [cell.is_today for cell in row.cells] == [False, False, True, False, False, False, False]
    # Volné dny se počítají jen dopředu - volno ve středu, co už byla, k ničemu není.
    assert row.free_days == 5
