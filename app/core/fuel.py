"""Palivo vs. elektřina - jednotky a názvosloví na jednom místě.

Ve vozovém parku jsou jak spalovací vozy, tak elektromobily. Pro řidiče je
to tentýž krok ("kolik jsem toho natankoval / nabil"), liší se jen
jednotka (l / kWh) a slovo (tankování / nabíjení). Kdyby se to rozhodovalo
v každé šabloně a routě zvlášť, dřív nebo později se někde objeví "litrů"
u elektromobilu.

Uložená data zůstávají pravdou: `TripFueling.unit` se ukládá explicitně,
takže pozdější přetypování vozidla z nafty na elektro nikdy zpětně
nepřeznačí historické záznamy. Tyhle funkce jen rozhodují, co se
uživateli PŘEDVYPLNÍ a jak se to pojmenuje.
"""

UNIT_LITERS = "l"
UNIT_KWH = "kWh"
UNITS = [UNIT_LITERS, UNIT_KWH]

# Čistě elektrické vozy. Hybrid schválně není v seznamu - plug-in hybrid
# opravdu dělá obojí, takže mu nabídneme obě jednotky s litry jako
# výchozí volbou.
ELECTRIC_FUEL_TYPES = {"elektro"}
DUAL_FUEL_TYPES = {"hybrid"}


def is_electric(fuel_type: str | None) -> bool:
    return fuel_type in ELECTRIC_FUEL_TYPES


def default_unit(fuel_type: str | None) -> str:
    """Jednotka předvyplněná ve formuláři tankování/nabíjení."""
    return UNIT_KWH if is_electric(fuel_type) else UNIT_LITERS


def available_units(fuel_type: str | None) -> list[str]:
    """Které jednotky smí uživatel u daného vozidla vybrat. U hybridu obě,
    jinak jedna - ale nikdy prázdný seznam, aby formulář vždy fungoval i u
    vozidla s nevyplněným typem paliva."""
    if fuel_type in DUAL_FUEL_TYPES:
        return [UNIT_LITERS, UNIT_KWH]
    return [default_unit(fuel_type)]


def refuel_noun(fuel_type: str | None) -> str:
    """„Tankování" / „Nabíjení" - nadpis sekce a názvy tlačítek."""
    return "Nabíjení" if is_electric(fuel_type) else "Tankování"


def refuel_verb(fuel_type: str | None) -> str:
    """„Natankováno" / „Nabito"."""
    return "Nabito" if is_electric(fuel_type) else "Natankováno"


def station_label(fuel_type: str | None) -> str:
    return "Nabíjecí stanice" if is_electric(fuel_type) else "Čerpací stanice"


def level_label(fuel_type: str | None) -> str:
    """Popisek pro current_fuel_level / start_fuel_level / end_fuel_level."""
    return "Stav baterie" if is_electric(fuel_type) else "Stav nádrže"


def quantity_label(unit: str) -> str:
    return "Nabito (kWh)" if unit == UNIT_KWH else "Natankováno (l)"


def price_per_unit_label(unit: str) -> str:
    return "Cena za kWh" if unit == UNIT_KWH else "Cena za litr"


def capacity_for(vehicle) -> tuple[float | None, str]:
    """Kapacita vozidla a její jednotka - nádrž v litrech, nebo baterie v
    kWh u elektromobilu. Slouží jen k orientační kontrole zadaného
    množství, nikdy k blokování."""
    if is_electric(vehicle.fuel_type):
        return vehicle.battery_capacity_kwh, UNIT_KWH
    return vehicle.tank_capacity_l, UNIT_LITERS


def format_quantity(quantity, unit: str) -> str:
    """„48,5 l" / „37,2 kWh" - do přehledů a e-mailů."""
    if quantity is None:
        return "-"
    number = f"{float(quantity):.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{number} {unit}"
