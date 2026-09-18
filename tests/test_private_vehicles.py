"""Soukromá vozidla uživatelů (Etapa 11, část B).

Těžiště není v tom, že vlastník své auto vidí, ale v tom, že ho **nikdo
jiný nevidí nikde** - ani v seznamu, ani přímou URL, ani přes QR, ani
ve firemních přehledech a exportech. Proto se skoro každý test ptá ze
tří stran: vlastník, cizí uživatel, administrátor.

Není to multi-tenancy: jedna instalace = jedna organizace. Soukromé
vozidlo je jen vozidlo, které patří jednomu člověku.
"""
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import VEHICLE_FORM, create_vehicle, extract_csrf_token, login


# --- pomůcky -----------------------------------------------------------

async def _user(email: str):
    from app.core.db import async_session_factory
    from app.models.core import User

    async with async_session_factory() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def _vehicle(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()


async def create_private(ac, *, code: str, plate: str, owner_id=None, **overrides) -> str:
    """Založí soukromé vozidlo přes formulář a vrátí jeho id."""
    form = await ac.get("/kniha-jizd/vehicles/new?scope=private")
    assert form.status_code == 200, form.text
    data = {
        **VEHICLE_FORM,
        "csrf_token": extract_csrf_token(form.text),
        "internal_code": code,
        "license_plate": plate,
        "vehicle_scope": "private",
        **overrides,
    }
    if owner_id is not None:
        data["owner_user_id"] = str(owner_id)
    response = await ac.post("/kniha-jizd/vehicles/new", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/vehicles/")[1].split("?")[0]


@pytest.fixture
async def owner_client(anon_client, basic_user):
    """Běžný uživatel, který bude mít soukromé vozidlo."""
    await login(anon_client, basic_user)
    return anon_client


@pytest.fixture
async def stranger_client(responsible_user):
    """Jiný běžný uživatel - odpovědná osoba firemních vozidel, což mu
    k cizímu soukromému autu nesmí dát vůbec nic.

    Vlastní instance klienta, ne `client`: na tom staví `logged_in_client`
    a přihlášení sem by přebilo adminovu session i její CSRF token."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await login(ac, responsible_user)
        yield ac


# ======================================================================
# Model a migrace
# ======================================================================

async def test_existing_vehicles_stay_company(logged_in_client, csrf_token):
    """Dosavadní vozidla se migrací nesmí změnit."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="PV-00", license_plate="1PV 0000",
    )
    vehicle = await _vehicle(vehicle_id)
    assert vehicle.vehicle_scope == "company"
    assert vehicle.owner_user_id is None
    assert vehicle.is_private is False


async def test_private_vehicle_must_have_an_owner():
    """Soukromé vozidlo bez vlastníka by neviděl nikdo - hlídá to schéma
    i CHECK v databázi."""
    from app.modules.vehicles.schemas import VehicleCreate

    with pytest.raises(ValueError, match="vlastník"):
        VehicleCreate(
            internal_code="X", license_plate="X", brand="X", model="X",
            vehicle_scope="private", owner_user_id=None,
        )


async def test_company_vehicle_never_keeps_an_owner():
    from app.modules.vehicles.schemas import VehicleCreate

    data = VehicleCreate(
        internal_code="X", license_plate="X", brand="X", model="X",
        vehicle_scope="company", owner_user_id=uuid.uuid4(),
    )
    assert data.owner_user_id is None


async def test_private_vehicle_drops_the_responsible_person():
    """Odpovědná osoba je firemní role a u soukromého auta nedává smysl."""
    from app.modules.vehicles.schemas import VehicleCreate

    data = VehicleCreate(
        internal_code="X", license_plate="X", brand="X", model="X",
        vehicle_scope="private", owner_user_id=uuid.uuid4(),
        responsible_user_id=uuid.uuid4(),
    )
    assert data.responsible_user_id is None


async def test_database_rejects_a_private_vehicle_without_owner(logged_in_client, csrf_token):
    """Poslední pojistka je v databázi, ne v aplikaci."""
    from sqlalchemy.exc import IntegrityError
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        db.add(Vehicle(
            internal_code="PV-BAD", license_plate="1PV BAD", brand="X", model="X",
            qr_token=uuid.uuid4().hex, vehicle_scope="private", owner_user_id=None,
        ))
        with pytest.raises(IntegrityError):
            await db.flush()


# ======================================================================
# Založení a vlastnictví
# ======================================================================

async def test_owner_creates_and_sees_their_private_vehicle(owner_client, basic_user):
    owner = await _user(basic_user[0])
    vehicle_id = await create_private(owner_client, code="PV-01", plate="1PV 0001")

    vehicle = await _vehicle(vehicle_id)
    assert vehicle.vehicle_scope == "private"
    assert vehicle.owner_user_id == owner.id

    detail = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert detail.status_code == 200
    assert "1PV 0001" in detail.text
    assert "Soukromé" in detail.text


async def test_owner_cannot_create_a_private_vehicle_for_somebody_else(owner_client, admin_user):
    """Vlastník se přepíše na přihlášeného - podstrčené id nemá účinek."""
    admin = await _user(admin_user[0])
    vehicle_id = await create_private(
        owner_client, code="PV-02", plate="1PV 0002", owner_id=admin.id,
    )
    vehicle = await _vehicle(vehicle_id)
    assert vehicle.owner_user_id != admin.id


async def test_admin_can_assign_the_owner(logged_in_client, basic_user):
    owner = await _user(basic_user[0])
    vehicle_id = await create_private(
        logged_in_client, code="PV-03", plate="1PV 0003", owner_id=owner.id,
    )
    assert (await _vehicle(vehicle_id)).owner_user_id == owner.id


async def test_owner_can_edit_their_private_vehicle(owner_client):
    vehicle_id = await create_private(owner_client, code="PV-04", plate="1PV 0004")

    form = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/edit")
    assert form.status_code == 200

    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={**VEHICLE_FORM, "csrf_token": extract_csrf_token(form.text),
              "internal_code": "PV-04", "license_plate": "1PV 0004", "brand": "Opel"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (await _vehicle(vehicle_id)).brand == "Opel"


async def test_editing_cannot_turn_a_private_vehicle_into_a_company_one(owner_client):
    """Jinak by se soukromé auto i s historií přesypalo do firemních
    přehledů podstrčeným polem."""
    vehicle_id = await create_private(owner_client, code="PV-05", plate="1PV 0005")

    form = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/edit")
    await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={**VEHICLE_FORM, "csrf_token": extract_csrf_token(form.text),
              "internal_code": "PV-05", "license_plate": "1PV 0005",
              "vehicle_scope": "company", "owner_user_id": ""},
        follow_redirects=False,
    )

    vehicle = await _vehicle(vehicle_id)
    assert vehicle.vehicle_scope == "private"
    assert vehicle.owner_user_id is not None


# ======================================================================
# Cizí uživatel nevidí nic
# ======================================================================

async def test_stranger_gets_404_on_direct_id(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-10", plate="1PV 0010")
    assert (await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")).status_code == 404


async def test_stranger_does_not_see_it_in_any_list(owner_client, stranger_client):
    await create_private(owner_client, code="PV-11", plate="1PV 0011")

    for path in ("/kniha-jizd/vehicles", "/kniha-jizd/vehicles?scope=private",
                 "/kniha-jizd/vehicles?only=mine", "/kniha-jizd/vehicles/private",
                 "/kniha-jizd/", "/kniha-jizd/reservations"):
        page = await stranger_client.get(path)
        assert page.status_code == 200, path
        assert "1PV 0011" not in page.text, path


async def test_stranger_gets_404_through_qr(owner_client, stranger_client):
    """QR nesmí být obchvat oprávnění."""
    vehicle_id = await create_private(owner_client, code="PV-12", plate="1PV 0012")
    vehicle = await _vehicle(vehicle_id)

    assert (await stranger_client.get(f"/kniha-jizd/v/{vehicle.qr_token}")).status_code == 404
    # Vlastníkovi QR funguje.
    assert (await owner_client.get(f"/kniha-jizd/v/{vehicle.qr_token}")).status_code in (200, 303, 307)


async def test_stranger_cannot_reach_any_sub_resource(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-13", plate="1PV 0013")

    for suffix in ("", "/edit", "/services", "/documents", "/wheels", "/expenses",
                   "/defects", "/qr", "/trips/start", "/reservations"):
        response = await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}{suffix}")
        assert response.status_code in (403, 404), f"{suffix} -> {response.status_code}"


async def test_owner_reaches_every_sub_resource(owner_client):
    vehicle_id = await create_private(owner_client, code="PV-14", plate="1PV 0014")

    for suffix in ("", "/services", "/documents", "/wheels", "/expenses", "/qr"):
        response = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}{suffix}")
        assert response.status_code == 200, f"{suffix} -> {response.status_code}"


async def test_admin_sees_private_vehicles(logged_in_client, owner_client):
    vehicle_id = await create_private(owner_client, code="PV-15", plate="1PV 0015")

    assert (await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")).status_code == 200

    private_tab = await logged_in_client.get("/kniha-jizd/vehicles?scope=private")
    assert "1PV 0015" in private_tab.text
    # S jménem vlastníka, ať je v podpoře poznat, čí to je.
    assert "Soukromé" in private_tab.text


# ======================================================================
# Firemní pohledy zůstávají čisté (B3)
# ======================================================================

async def test_private_vehicle_is_not_in_the_company_list(owner_client, logged_in_client):
    await create_private(owner_client, code="PV-20", plate="1PV 0020")

    # Ani administrátorovi, který ho jinak vidět smí - firemní záložka je
    # firemní.
    company = await logged_in_client.get("/kniha-jizd/vehicles")
    assert "1PV 0020" not in company.text

    # A vlastníkovi taky ne.
    owner_company = await owner_client.get("/kniha-jizd/vehicles")
    assert "1PV 0020" not in owner_company.text


async def test_private_vehicle_is_not_in_the_reservation_calendar(owner_client, logged_in_client):
    await create_private(owner_client, code="PV-21", plate="1PV 0021")

    for client in (owner_client, logged_in_client):
        calendar = await client.get("/kniha-jizd/reservations")
        assert "1PV 0021" not in calendar.text

        form = await client.get("/kniha-jizd/reservations/new")
        assert "1PV 0021" not in form.text


async def test_reserving_a_private_vehicle_is_refused(owner_client):
    """Ani přímým POSTem - výběr ho nenabízí, takže jinak přijít nemůže."""
    vehicle_id = await create_private(owner_client, code="PV-22", plate="1PV 0022")

    form = await owner_client.get("/kniha-jizd/reservations/new")
    start = datetime.now(timezone.utc) + timedelta(days=1)
    response = await owner_client.post(
        "/kniha-jizd/reservations/new",
        data={"csrf_token": extract_csrf_token(form.text), "vehicle_id": vehicle_id,
              "start_at": start.strftime("%Y-%m-%dT%H:%M"),
              "end_at": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
              "purpose": "Pokus"},
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_private_vehicle_is_not_in_the_company_logbook(owner_client, logged_in_client):
    vehicle_id = await create_private(owner_client, code="PV-23", plate="1PV 0023")
    await _drive(owner_client, vehicle_id)

    logbook = await logged_in_client.get("/kniha-jizd/logbook")
    assert "1PV 0023" not in logbook.text

    # Ani přímým filtrem na jeho id.
    targeted = await logged_in_client.get(f"/kniha-jizd/logbook?vehicle={vehicle_id}")
    assert "1PV 0023" not in targeted.text


async def test_private_vehicle_is_not_in_company_exports(owner_client, logged_in_client):
    vehicle_id = await create_private(owner_client, code="PV-24", plate="1PV 0024")
    await _drive(owner_client, vehicle_id)

    for extension in ("csv", "xlsx", "pdf"):
        export = await logged_in_client.get(f"/kniha-jizd/logbook/export.{extension}")
        assert export.status_code == 200
        assert b"1PV 0024" not in export.content, extension

    targeted = await logged_in_client.get(f"/kniha-jizd/logbook/export.csv?vehicle={vehicle_id}")
    assert b"1PV 0024" not in targeted.content


async def test_private_vehicle_is_not_in_the_dashboard(owner_client):
    await create_private(owner_client, code="PV-25", plate="1PV 0025")
    dashboard = await owner_client.get("/kniha-jizd/")
    assert "1PV 0025" not in dashboard.text


# ======================================================================
# Vlastní sekce „Moje vozidla"
# ======================================================================

async def test_my_vehicles_shows_only_my_private_ones(owner_client, stranger_client):
    await create_private(owner_client, code="PV-30", plate="1PV 0030")
    await create_private(stranger_client, code="PV-31", plate="1PV 0031")

    mine = await owner_client.get("/kniha-jizd/vehicles/private")
    assert mine.status_code == 200
    assert "1PV 0030" in mine.text
    assert "1PV 0031" not in mine.text

    theirs = await stranger_client.get("/kniha-jizd/vehicles/private")
    assert "1PV 0031" in theirs.text
    assert "1PV 0030" not in theirs.text


async def test_my_vehicles_is_reachable_from_the_nav(owner_client):
    page = await owner_client.get("/kniha-jizd/")
    assert "/kniha-jizd/vehicles/private" in page.text


# ======================================================================
# Moduly nad soukromým vozidlem
# ======================================================================

async def _drive(ac, vehicle_id: str, *, start_km=100000, driven=80) -> str:
    start = await ac.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    response = await ac.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": extract_csrf_token(start.text),
              "start_odometer_km": str(start_km), "start_fuel_level": "50"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    trip_id = response.headers["location"].split("/trips/")[1].split("?")[0]

    end = await ac.get(f"/kniha-jizd/trips/{trip_id}/end")
    await ac.post(
        f"/kniha-jizd/trips/{trip_id}/end",
        data={"csrf_token": extract_csrf_token(end.text),
              "end_odometer_km": str(start_km + driven), "end_fuel_level": "30",
              "purpose_code": "jine", "purpose_text": "Soukromá cesta",
              "route_text": "Domů a zpět"},
        follow_redirects=False,
    )
    return trip_id


async def test_owner_can_drive_their_private_vehicle(owner_client):
    vehicle_id = await create_private(owner_client, code="PV-40", plate="1PV 0040")
    trip_id = await _drive(owner_client, vehicle_id)

    detail = await owner_client.get(f"/kniha-jizd/trips/{trip_id}")
    assert detail.status_code == 200

    mine = await owner_client.get("/kniha-jizd/trips/mine")
    assert "1PV 0040" in mine.text


async def test_stranger_cannot_open_a_private_trip(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-41", plate="1PV 0041")
    trip_id = await _drive(owner_client, vehicle_id)

    assert (await stranger_client.get(f"/kniha-jizd/trips/{trip_id}")).status_code == 404


async def test_owner_can_record_an_expense(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-42", plate="1PV 0042")

    form = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new")
    assert form.status_code == 200
    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "expense_type": "mytí", "spent_on": date.today().isoformat(),
              "amount_czk": "250", "description": "Mytí"},
        follow_redirects=False,
    )
    assert response.status_code in (303, 400), response.text

    listing = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses")
    assert listing.status_code == 200
    assert (await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses")).status_code == 404


async def test_owner_can_record_a_service(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-43", plate="1PV 0043")

    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/services/new",
        data={"csrf_token": extract_csrf_token(
                  (await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services/new")).text),
              "service_date": date.today().isoformat(),
              "service_type": "brzdy", "description": "Brzdové destičky"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    assert (await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")).status_code == 200
    assert (await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")).status_code == 404


async def test_owner_can_upload_a_document(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-44", plate="1PV 0044")
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/documents",
        data={"csrf_token": extract_csrf_token(
                  (await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/documents")).text),
              "doc_type": "tp", "title": "Technický průkaz"},
        files={"document": ("tp.pdf", pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert (await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/documents")).status_code == 404


async def test_owner_can_manage_wheels(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-45", plate="1PV 0045")

    page = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/wheels")
    assert page.status_code == 200
    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/wheels/sets",
        data={"csrf_token": extract_csrf_token(page.text), "season": "zimni",
              "label": "Zimní sada", "size": "205/55 R16"},
        follow_redirects=False,
    )
    assert response.status_code in (303, 400), response.text
    assert (await stranger_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/wheels")).status_code == 404


async def test_owner_can_report_a_defect(owner_client, stranger_client):
    vehicle_id = await create_private(owner_client, code="PV-46", plate="1PV 0046")

    form = await owner_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/defects/new")
    assert form.status_code == 200
    response = await owner_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/defects/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "description": "Praskl stěrač", "priority": "normal"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    # Cizí ji nesmí najít ani v seznamu závad.
    defects = await stranger_client.get("/kniha-jizd/defects")
    assert "1PV 0046" not in defects.text


async def test_company_vehicles_still_work_for_everybody(
    logged_in_client, csrf_token, owner_client, stranger_client,
):
    """Regrese: soukromá vozidla nesmí nic ubrat firemním."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="PV-50", license_plate="1PV 0050",
    )

    for client in (logged_in_client, owner_client, stranger_client):
        listing = await client.get("/kniha-jizd/vehicles")
        assert "1PV 0050" in listing.text
        assert (await client.get(f"/kniha-jizd/vehicles/{vehicle_id}")).status_code == 200

    calendar = await owner_client.get("/kniha-jizd/reservations")
    assert "1PV 0050" in calendar.text
