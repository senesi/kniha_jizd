"""Per-vehicle authorization, in one place.

Every route that touches a vehicle's configuration, documents, service
records, defects or photos goes through `assert_vehicle_manage_access`.
The UI hides buttons somebody cannot use, but this is the actual
enforcement - it is called after the vehicle is loaded, so it can compare
against THAT vehicle's own responsible_user_id (a route-level dependency
runs before anything is fetched and could never do that).

Two permission codes, mirroring zadání 4:
  fleet.vehicle.manage      - administrátor, any vehicle, unscoped
  fleet.vehicle.manage.own  - odpovědná osoba, only vehicles where
                              responsible_user_id == the actor
"""
from fastapi import HTTPException, status
from sqlalchemy import or_

from app.models.core import User
from app.models.fleet import Vehicle

MANAGE_ANY = "fleet.vehicle.manage"
MANAGE_OWN = "fleet.vehicle.manage.own"


def can_manage_vehicle(codes: set[str], vehicle: Vehicle, user: User) -> bool:
    if MANAGE_ANY in codes:
        return True
    return MANAGE_OWN in codes and vehicle.responsible_user_id == user.id


def assert_vehicle_manage_access(codes: set[str], vehicle: Vehicle, user: User) -> None:
    if not can_manage_vehicle(codes, vehicle, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nemáš oprávnění spravovat toto vozidlo.",
        )


# --- viditelnost vozidla (požadavek D) --------------------------------
#
# "restricted" vozidlo pro běžného uživatele NEEXISTUJE: nesmí se objevit
# v seznamu, na přehledu, ve výběru vozidla, v kalendáři ani přes QR kód.
# Proto se nefiltruje až v šabloně, ale přímo v dotazu (podmínka níž) a
# u jednotlivého vozidla se kontroluje hned po načtení.

VISIBILITY_ALL = "all"
VISIBILITY_RESTRICTED = "restricted"

# Kdo vidí i skrytá vozidla bez ohledu na odpovědnost. Jen administrátor
# (požadavek D: „viditelné pouze pro odpovědnou osobu a administrátory").
#
# Schválně tu NENÍ fleet.logbook.view: to je oprávnění ke knize jízd
# napříč vozidly, ne k obcházení viditelnosti. Má ho i odpovědná osoba,
# takže by jinak viděla i cizí skrytá vozidla.
SEE_ALL_CODES = {MANAGE_ANY}


def sees_every_vehicle(codes: set[str]) -> bool:
    return bool(SEE_ALL_CODES & codes)


def can_view_vehicle(codes: set[str], vehicle: Vehicle, user: User) -> bool:
    if vehicle.visibility != VISIBILITY_RESTRICTED:
        return True
    if sees_every_vehicle(codes):
        return True
    return vehicle.responsible_user_id == user.id


def assert_vehicle_visible(codes: set[str], vehicle: Vehicle, user: User) -> None:
    """404, ne 403. U skrytého vozidla je i samotná informace „tohle
    vozidlo existuje, jen na něj nemáš právo" únikem - a přes QR token by
    se tím dalo ověřovat, která nálepka patří kterému autu."""
    if not can_view_vehicle(codes, vehicle, user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vozidlo nebylo nalezeno.",
        )


def visible_vehicles_condition(codes: set[str], user: User):
    """Podmínka do WHERE pro každý výpis vozidel. Vrací None, když uživatel
    vidí všechno - volající ji pak prostě nepřidá."""
    if sees_every_vehicle(codes):
        return None
    return or_(
        Vehicle.visibility != VISIBILITY_RESTRICTED,
        Vehicle.responsible_user_id == user.id,
    )
