"""Semafor termínů a názvosloví palivo/elektřina - čisté funkce.

Bez databáze a bez HTTP: tohle jsou výpočty, které se používají na kartě
vozidla, v seznamu, na dashboardu i v notifikacích, takže se vyplatí je
mít ověřené přímo, ne jen skrz vykreslenou stránku.
"""
from datetime import date

import pytest

from app.core import fuel
from app.core.app_settings import DEFAULTS
from app.core.fleet_status import (
    insurance_status,
    oil_status,
    stk_status,
    vehicle_deadlines,
    vignette_status,
    worst_level,
)
from app.models.fleet import Vehicle

TODAY = date(2026, 9, 16)


def _vehicle(**kwargs) -> Vehicle:
    defaults = dict(current_odometer_km=100000, fuel_type="nafta")
    return Vehicle(**{**defaults, **kwargs})


# --- termíny s datem ---------------------------------------------------

@pytest.mark.parametrize(
    "due, expected",
    [
        (date(2027, 6, 1), "green"),     # daleko
        (date(2026, 10, 1), "orange"),   # 15 dní, práh je 30
        (date(2026, 9, 16), "orange"),   # dnes je poslední den platnosti
        (date(2026, 9, 15), "red"),      # včera propadlo
    ],
)
def test_stk_traffic_light(due, expected):
    assert stk_status(_vehicle(stk_valid_until=due), DEFAULTS, TODAY).level == expected


def test_missing_date_is_unknown_not_green():
    """Nevyplněný termín se nesmí tvářit jako v pořádku."""
    status = stk_status(_vehicle(stk_valid_until=None), DEFAULTS, TODAY)
    assert status.level == "unknown"
    assert "není zadán" in status.detail


def test_vignette_uses_its_own_threshold():
    """Známka má kratší práh (14 dní) než STK (30) - 20 dní je u známky
    ještě zelená, u STK už oranžová."""
    due = date(2026, 10, 6)
    assert vignette_status(_vehicle(vignette_valid_until=due), DEFAULTS, TODAY).level == "green"
    assert stk_status(_vehicle(stk_valid_until=due), DEFAULTS, TODAY).level == "orange"


def test_insurance_is_watched_like_stk():
    vehicle = _vehicle(insurance_valid_until=date(2026, 9, 10))
    status = insurance_status(vehicle, DEFAULTS, TODAY)
    assert status.level == "red"
    assert status.label == "Pojištění"
    assert "Po termínu" in status.detail


def test_all_four_deadlines_are_shown():
    codes = [status.code for status in vehicle_deadlines(_vehicle(), DEFAULTS, TODAY)]
    assert codes == ["stk", "insurance", "vignette", "oil"]


def test_worst_level_picks_the_most_urgent():
    statuses = vehicle_deadlines(
        _vehicle(
            stk_valid_until=date(2027, 1, 1),        # green
            insurance_valid_until=date(2026, 9, 20),  # red? ne - 4 dny -> orange
            vignette_valid_until=date(2026, 9, 1),    # red
        ),
        DEFAULTS, TODAY,
    )
    assert worst_level(statuses) == "red"


# --- olej: km i čas ----------------------------------------------------

def test_oil_without_interval_is_unknown():
    assert oil_status(_vehicle(), DEFAULTS, TODAY).level == "unknown"


def test_oil_by_km_interval():
    vehicle = _vehicle(current_odometer_km=100000, last_oil_change_km=90000, oil_interval_km=15000)
    assert oil_status(vehicle, DEFAULTS, TODAY).level == "green"       # zbývá 5000 km

    vehicle.current_odometer_km = 104500                                # zbývá 500 km, práh 1000
    assert oil_status(vehicle, DEFAULTS, TODAY).level == "orange"

    vehicle.current_odometer_km = 106000                                # po termínu
    status = oil_status(vehicle, DEFAULTS, TODAY)
    assert status.level == "red"
    assert status.km_left == -1000


def test_oil_by_time_interval():
    vehicle = _vehicle(last_oil_change_at=date(2025, 9, 1), oil_interval_months=12)
    status = oil_status(vehicle, DEFAULTS, TODAY)
    assert status.level == "red"       # mělo být 1.9.2026
    assert status.due_date == date(2026, 9, 1)


def test_oil_takes_whichever_interval_comes_first():
    """Km interval je v pohodě, ale časový už propadl - platí ten horší."""
    vehicle = _vehicle(
        current_odometer_km=91000, last_oil_change_km=90000, oil_interval_km=15000,
        last_oil_change_at=date(2025, 1, 1), oil_interval_months=12,
    )
    assert oil_status(vehicle, DEFAULTS, TODAY).level == "red"


def test_month_arithmetic_clamps_to_last_valid_day():
    """31.1. + 1 měsíc nesmí spadnout na neexistující 31.2."""
    vehicle = _vehicle(last_oil_change_at=date(2026, 1, 31), oil_interval_months=1)
    assert oil_status(vehicle, DEFAULTS, TODAY).due_date == date(2026, 2, 28)


# --- palivo vs. elektřina ----------------------------------------------

def test_electric_vehicle_uses_kwh():
    assert fuel.default_unit("elektro") == "kWh"
    assert fuel.available_units("elektro") == ["kWh"]
    assert fuel.refuel_noun("elektro") == "Nabíjení"
    assert fuel.level_label("elektro") == "Stav baterie"
    assert fuel.station_label("elektro") == "Nabíjecí stanice"


def test_combustion_vehicle_uses_liters():
    assert fuel.default_unit("nafta") == "l"
    assert fuel.available_units("benzin") == ["l"]
    assert fuel.refuel_noun("nafta") == "Tankování"
    assert fuel.level_label("nafta") == "Stav nádrže"


def test_hybrid_offers_both_units():
    assert fuel.available_units("hybrid") == ["l", "kWh"]
    assert fuel.default_unit("hybrid") == "l"


def test_unknown_fuel_type_still_gets_a_unit():
    """Vozidlo s nevyplněným palivem nesmí formulář rozbít."""
    assert fuel.available_units(None) == ["l"]


def test_capacity_follows_the_drivetrain():
    ev = _vehicle(fuel_type="elektro", battery_capacity_kwh=77, tank_capacity_l=None)
    assert fuel.capacity_for(ev) == (77, "kWh")

    diesel = _vehicle(fuel_type="nafta", tank_capacity_l=50)
    assert fuel.capacity_for(diesel) == (50, "l")


def test_format_quantity_is_czech():
    assert fuel.format_quantity(48.5, "l") == "48,5 l"
    assert fuel.format_quantity(37.25, "kWh") == "37,25 kWh"
    assert fuel.format_quantity(None, "l") == "-"
