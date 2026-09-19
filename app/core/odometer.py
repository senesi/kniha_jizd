"""Posun stavu tachometru vozidla, na jednom místě.

Stav tachometru posouvá **uzavřená jízda, tankování i servisní záznam** —
u vozidel, která knihu jízd nevedou, jsou ty druhé dvě jediný zdroj.
Bez toho by jim tachometr zamrzl na počáteční hodnotě a přestal by
fungovat semafor servisní prohlídky, který se počítá proti aktuálnímu
stavu (`app/core/fleet_status.py:oil_status`).

Pravidlo je pro všechny tři stejné a je tu proto jednou, ne třikrát:
**běžná operace stav nikdy nesnižuje.** Snížit ho umí výhradně
administrativní oprava se zdůvodněním
(`vehicles/service.py:correct_odometer`, zadání 5/32).
"""
from datetime import datetime, timezone

from app.models.fleet import Vehicle


def advance(vehicle: Vehicle, odometer_km: int | None, *, fuel_level: int | None = None) -> bool:
    """Posune stav vozidla, pokud je zadaná hodnota vyšší.

    Vrací `True`, když se stav opravdu pohnul - volající to používá do
    auditu. `None` je „neuvedeno" a nedělá nic; nižší hodnota taky ne,
    a to je záměr, ne opomenutí."""
    moved = False
    if odometer_km is not None and odometer_km > vehicle.current_odometer_km:
        vehicle.current_odometer_km = odometer_km
        moved = True
    if fuel_level is not None:
        vehicle.current_fuel_level = fuel_level
        moved = True
    if moved:
        vehicle.state_updated_at = datetime.now(timezone.utc)
    return moved
