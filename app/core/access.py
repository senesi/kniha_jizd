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
from sqlalchemy import and_, or_

from app.models.core import User
from app.models.fleet import Vehicle

MANAGE_ANY = "fleet.vehicle.manage"
MANAGE_OWN = "fleet.vehicle.manage.own"


def can_manage_vehicle(codes: set[str], vehicle: Vehicle, user: User) -> bool:
    if MANAGE_ANY in codes:
        return True
    # Vlastník spravuje své soukromé vozidlo v plném rozsahu - dokumenty,
    # servis, kola, výdaje. Bez tohohle by si k vlastnímu autu nemohl
    # zapsat ani výměnu oleje a modul by pro něj byl k ničemu.
    if owns_vehicle(vehicle, user) and MANAGE_PRIVATE in codes:
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

# Rozsah vozidla (Etapa 11). Firemní = dosavadní stav, soukromé = vozidlo
# jednoho uživatele uvnitř téže instalace. NENÍ to multi-tenancy; je to
# jen druhý druh vozidla, který nepatří do firemních pohledů.
SCOPE_COMPANY = "company"
SCOPE_PRIVATE = "private"
SCOPE_ANY = "any"

# Oprávnění „smím mít vlastní soukromé vozidlo". Mají ho všechny role;
# o konkrétním vozidle rozhoduje porovnání owner_user_id proti
# přihlášenému, ne role - stejně jako u MANAGE_OWN.
MANAGE_PRIVATE = "fleet.vehicle.private"

# Kdo vidí i skrytá vozidla bez ohledu na odpovědnost. Jen administrátor
# (požadavek D: „viditelné pouze pro odpovědnou osobu a administrátory").
#
# Schválně tu NENÍ fleet.logbook.view: to je oprávnění ke knize jízd
# napříč vozidly, ne k obcházení viditelnosti. Má ho i odpovědná osoba,
# takže by jinak viděla i cizí skrytá vozidla.
SEE_ALL_CODES = {MANAGE_ANY}


def sees_every_vehicle(codes: set[str]) -> bool:
    return bool(SEE_ALL_CODES & codes)


def owns_vehicle(vehicle: Vehicle, user: User) -> bool:
    """Soukromé vozidlo tohohle uživatele."""
    return vehicle.vehicle_scope == SCOPE_PRIVATE and vehicle.owner_user_id == user.id


def can_view_vehicle(codes: set[str], vehicle: Vehicle, user: User) -> bool:
    if vehicle.vehicle_scope == SCOPE_PRIVATE:
        # Soukromé vozidlo je věc svého vlastníka. Administrátor ho vidí
        # kvůli podpoře; odpovědná osoba ani nikdo jiný ne - „odpovědná
        # osoba" je firemní role a k cizímu soukromému autu nic neříká.
        return owns_vehicle(vehicle, user) or sees_every_vehicle(codes)
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


def _company_condition(codes: set[str], user: User):
    """Firemní vozidla podle dosavadních pravidel viditelnosti."""
    company = Vehicle.vehicle_scope == SCOPE_COMPANY
    if sees_every_vehicle(codes):
        return company
    return and_(company, or_(
        Vehicle.visibility != VISIBILITY_RESTRICTED,
        Vehicle.responsible_user_id == user.id,
    ))


def _private_condition(codes: set[str], user: User):
    """Soukromá vozidla: svoje, administrátor všechna."""
    private = Vehicle.vehicle_scope == SCOPE_PRIVATE
    if sees_every_vehicle(codes):
        return private
    return and_(private, Vehicle.owner_user_id == user.id)


def assert_company_vehicle(vehicle: Vehicle) -> None:
    """Pro firemní procesy, do kterých soukromé vozidlo nepatří.

    Rezervace a schvalování služební jízdy jsou o sdílení firemního
    majetku. U vlastního auta nedávají smysl a zadání je pro soukromá
    vozidla výslovně nechce (B8).

    404, ne 400: výběr vozidla soukromá auta vůbec nenabízí, takže
    požadavek na ně může přijít jen ručně sestaveným POSTem. Chová se
    stejně jako u skrytého vozidla - tahle cesta prostě neexistuje."""
    if vehicle.vehicle_scope == SCOPE_PRIVATE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vozidlo nebylo nalezeno.",
        )


def visible_vehicles_condition(codes: set[str], user: User, *, scope: str = SCOPE_COMPANY):
    """Podmínka do WHERE pro každý výpis vozidel.

    `scope` rozhoduje, o jaký pohled jde:

    - `company` (**výchozí**) — jen firemní vozidla. Kalendář rezervací,
      kniha jízd, firemní přehledy a exporty. Soukromé vozidlo se do
      firemního pohledu nesmí dostat ani vlastníkovi: je jeho, ne firmy.
    - `private` — soukromá vozidla přihlášeného (administrátorovi
      všechna). Sekce „Moje vozidla".
    - `any` — obojí podle příslušných pravidel. Použít jen tam, kde se
      opravdu pracuje s libovolným vozidlem, které uživatel vidí.

    Výchozí hodnota je schválně `company`: kdo na tenhle parametr
    zapomene, dostane firemní pohled, ne únik soukromých vozidel.
    Dřív funkce vracela `None` pro administrátora („vidí všechno"),
    teď vrací podmínku vždy — jinak by se rozsah neuplatnil."""
    if scope == SCOPE_PRIVATE:
        return _private_condition(codes, user)
    if scope == SCOPE_ANY:
        return or_(_company_condition(codes, user), _private_condition(codes, user))
    return _company_condition(codes, user)
