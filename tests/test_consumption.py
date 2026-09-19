"""Výpočet průměrné spotřeby - čisté funkce bez databáze (zadání 15)."""
from dataclasses import dataclass
from datetime import date, timedelta

from app.core import consumption


@dataclass
class FakeFueling:
    odometer_km: int | None
    quantity: float | None
    unit: str = "l"
    fueled_at: date = date(2026, 1, 1)


def _series(*pairs, unit="l"):
    """(km, objem) dvojice s rostoucím datem."""
    return [
        FakeFueling(km, quantity, unit, date(2026, 1, 1) + timedelta(days=index))
        for index, (km, quantity) in enumerate(pairs)
    ]


# --- základní výpočet --------------------------------------------------

def test_first_volume_is_excluded():
    """To palivo bylo v nádrži ještě před prvním odečtem km.

    1000 km, natankováno 50 + 50 l po prvním tankování = 100 l / 1000 km
    = 10 l/100 km. Kdyby se počítal i první objem, vyšlo by 15."""
    result = consumption.calculate(_series((10000, 50), (10500, 50), (11000, 50)))

    assert result is not None
    assert result.per_100km == 10.0
    assert result.distance_km == 1000
    assert result.total_quantity == 100.0
    assert result.fueling_count == 3


def test_two_fuelings_are_enough():
    result = consumption.calculate(_series((10000, 40), (10400, 30)))
    assert result is not None
    assert result.per_100km == 7.5   # 30 l / 400 km


def test_one_fueling_cannot_be_measured():
    """Jedno tankování nedává rozdíl km - vrací None, ne nulu.

    Nula by na kartě vozidla vypadala jako změřená hodnota."""
    assert consumption.calculate(_series((10000, 50))) is None
    assert consumption.calculate([]) is None


def test_fuelings_without_odometer_are_skipped():
    """Stav tachometru je ten údaj, na kterém vzorec stojí."""
    records = _series((10000, 50), (10500, 50))
    records.append(FakeFueling(None, 40, "l", date(2026, 3, 1)))

    result = consumption.calculate(records)
    assert result is not None
    assert result.fueling_count == 2
    assert result.total_quantity == 50.0


def test_records_are_ordered_by_odometer_not_by_insertion():
    """Zapsat je může kdokoliv v jakémkoliv pořadí."""
    shuffled = [
        FakeFueling(11000, 50, "l", date(2026, 1, 3)),
        FakeFueling(10000, 50, "l", date(2026, 1, 1)),
        FakeFueling(10500, 50, "l", date(2026, 1, 2)),
    ]
    result = consumption.calculate(shuffled)
    assert result.first_odometer_km == 10000
    assert result.last_odometer_km == 11000
    assert result.per_100km == 10.0


def test_zero_distance_is_not_a_division_by_zero():
    assert consumption.calculate(_series((10000, 50), (10000, 40))) is None


def test_zero_quantity_after_the_first_gives_nothing():
    assert consumption.calculate(_series((10000, 50), (10500, 0))) is None


# --- důvěryhodnost -----------------------------------------------------

def test_short_series_is_flagged_as_unreliable():
    """Průměr ze dvou tankování na padesáti kilometrech nic neříká -
    obrazovka to má rozlišit, ne to schovat."""
    short = consumption.calculate(_series((10000, 40), (10050, 5)))
    assert short is not None
    assert short.is_reliable is False

    solid = consumption.calculate(_series((10000, 50), (10500, 40), (11000, 40), (11500, 40)))
    assert solid.is_reliable is True


# --- jednotky ----------------------------------------------------------

def test_electric_uses_kwh():
    result = consumption.calculate(_series((5000, 40), (5300, 60), unit="kWh"), unit="kWh")
    assert result.unit == "kWh"
    assert result.per_100km == 20.0   # 60 kWh / 300 km


def test_litres_and_kwh_never_get_added_together():
    """Plug-in hybrid má dvě čísla, ne jedno nesmyslné."""
    mixed = _series((10000, 40), (10400, 20)) + _series((10000, 30), (10400, 15), unit="kWh")
    results = consumption.for_vehicle(mixed)

    assert [r.unit for r in results] == ["l", "kWh"]
    assert results[0].per_100km == 5.0    # 20 l / 400 km
    assert results[1].per_100km == 3.75   # 15 kWh / 400 km


def test_vehicle_with_nothing_measurable_returns_empty():
    assert consumption.for_vehicle([]) == []
    assert consumption.for_vehicle(_series((10000, 50))) == []
