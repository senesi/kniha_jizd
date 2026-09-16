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
