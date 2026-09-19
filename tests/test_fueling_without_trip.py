"""Tankování bez jízdy a posun tachometru (zadání 5/15).

U části vozidel se kniha jízd nevede a eviduje se jen tankování a
servis. Z toho plyne dvojí: musí jít zapsat tankování bez jízdy, a musí
z něj (i ze servisu) růst stav tachometru — jinak by u takového vozidla
zamrzl a přestal by fungovat semafor servisní prohlídky.
"""
from datetime import date

import pytest
from sqlalchemy import select

from tests.conftest import create_vehicle, extract_csrf_token, login


async def _vehicle(vehicle_id: str):
    import uuid
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(
            select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id))
        )).scalar_one()


async def _fuelings(vehicle_id: str):
    import uuid
    from app.core.db import async_session_factory
    from app.models.fleet import TripFueling

    async with async_session_factory() as db:
        return list((await db.execute(
            select(TripFueling)
            .where(TripFueling.vehicle_id == uuid.UUID(vehicle_id))
            .order_by(TripFueling.odometer_km)
        )).scalars().all())


async def _add(ac, vehicle_id: str, *, km, quantity, when=None, **extra):
    form = await ac.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")
    assert form.status_code == 200, form.text
    data = {
        "csrf_token": extract_csrf_token(form.text),
        "fueled_at": (when or date.today()).isoformat(),
        "quantity": str(quantity),
        "odometer_km": str(km) if km is not None else "",
        **extra,
    }
    return await ac.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new", data=data, follow_redirects=False,
    )


# ======================================================================
# Zápis bez jízdy
# ======================================================================

async def test_fueling_without_a_trip_is_saved(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-01", license_plate="1FT 0001",
    )
    response = await _add(logged_in_client, vehicle_id, km=100500, quantity="45,5")
    assert response.status_code == 303, response.text

    records = await _fuelings(vehicle_id)
    assert len(records) == 1
    assert records[0].trip_id is None, "tankování mimo jízdu nemá jízdu"
    assert records[0].vehicle_id is not None, "vozidlo musí být vyplněné vždycky"
    assert float(records[0].quantity) == 45.5
    assert records[0].odometer_km == 100500


async def test_odometer_is_required_without_a_trip(logged_in_client, csrf_token):
    """Bez něj by záznam byl k ničemu právě u vozidel, kvůli kterým
    tahle cesta vznikla."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-02", license_plate="1FT 0002",
    )
    response = await _add(logged_in_client, vehicle_id, km=None, quantity="40")
    assert response.status_code == 400
    assert "stav tachometru povinný" in response.text
    assert await _fuelings(vehicle_id) == []


async def test_odometer_stays_optional_inside_a_trip(logged_in_client, csrf_token):
    """Dosavadní chování u jízd se nesmí změnit."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-03", license_plate="1FT 0003",
    )
    start = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    trip = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": extract_csrf_token(start.text),
              "start_odometer_km": "100000", "start_fuel_level": "50"},
        follow_redirects=False,
    )
    trip_id = trip.headers["location"].split("/trips/")[1].split("?")[0]

    form = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}/fuelings/new")
    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(), "quantity": "30"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    records = await _fuelings(vehicle_id)
    assert records[0].odometer_km is None
    assert records[0].trip_id is not None


# ======================================================================
# Posun tachometru
# ======================================================================

async def test_fueling_moves_the_vehicle_odometer(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-10", license_plate="1FT 0010",
        current_odometer_km="100000",
    )
    await _add(logged_in_client, vehicle_id, km=101200, quantity="50")

    assert (await _vehicle(vehicle_id)).current_odometer_km == 101200


async def test_fueling_never_lowers_the_odometer(logged_in_client, csrf_token):
    """Snížit stav umí výhradně administrativní oprava (zadání 5/32)."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-11", license_plate="1FT 0011",
        current_odometer_km="100000",
    )
    # Nižší hodnota = varování, které se musí odkliknout.
    warned = await _add(logged_in_client, vehicle_id, km=99000, quantity="40")
    assert warned.status_code == 200
    assert "nižší než poslední známý" in warned.text

    confirmed = await _add(
        logged_in_client, vehicle_id, km=99000, quantity="40", confirm="odometer_lower",
    )
    assert confirmed.status_code == 303, confirmed.text

    # Záznam vznikl, ale stav vozidla zůstal.
    assert len(await _fuelings(vehicle_id)) == 1
    assert (await _vehicle(vehicle_id)).current_odometer_km == 100000


async def test_service_record_moves_the_odometer(logged_in_client, csrf_token):
    """Druhá polovina toho, co vozidla bez knihy jízd potřebují."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-12", license_plate="1FT 0012",
        current_odometer_km="100000",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services/new")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/services/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "service_date": date.today().isoformat(), "service_type": "brzdy",
              "description": "Brzdové destičky", "odometer_km": "102500"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert (await _vehicle(vehicle_id)).current_odometer_km == 102500


async def test_service_never_lowers_the_odometer(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-13", license_plate="1FT 0013",
        current_odometer_km="100000",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services/new")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/services/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "service_date": date.today().isoformat(), "service_type": "brzdy",
              "description": "Zpětně dopsaný servis", "odometer_km": "90000",
              "confirm": "odometer_far"},
        follow_redirects=False,
    )
    assert (await _vehicle(vehicle_id)).current_odometer_km == 100000


# ======================================================================
# Spotřeba na obrazovce
# ======================================================================

async def test_consumption_appears_after_two_fuelings(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-20", license_plate="1FT 0020",
        current_odometer_km="100000",
    )
    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings")
    assert page.status_code == 200
    assert "Průměrná spotřeba" not in page.text

    await _add(logged_in_client, vehicle_id, km=100000, quantity="50")
    await _add(logged_in_client, vehicle_id, km=100500, quantity="40")

    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings")
    assert "Průměrná spotřeba" in page.text
    # 40 l / 500 km = 8 l/100 km (první objem se nepočítá).
    assert "8" in page.text
    assert "l/100 km" in page.text


async def test_consumption_is_on_the_vehicle_card(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-21", license_plate="1FT 0021",
        current_odometer_km="100000",
    )
    await _add(logged_in_client, vehicle_id, km=100000, quantity="50")
    await _add(logged_in_client, vehicle_id, km=101000, quantity="60")

    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert card.status_code == 200
    assert "l/100 km" in card.text


async def test_electric_vehicle_charges_without_a_trip(logged_in_client, csrf_token):
    """Elektromobil nabíjený přes noc v depu žádnou jízdu nemá."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-22", license_plate="1FT 0022",
        fuel_type="elektro", current_odometer_km="50000",
    )
    assert (await _add(logged_in_client, vehicle_id, km=50000, quantity="40")).status_code == 303
    assert (await _add(logged_in_client, vehicle_id, km=50300, quantity="60")).status_code == 303

    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings")
    assert "kWh/100 km" in page.text
    assert "Nabíjení" in page.text

    records = await _fuelings(vehicle_id)
    assert all(record.unit == "kWh" for record in records)


# ======================================================================
# Oprávnění
# ======================================================================

async def test_hidden_vehicle_is_not_reachable_through_fuelings(
    logged_in_client, csrf_token, anon_client, basic_user,
):
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="FT-30", license_plate="1FT 0030",
        visibility="restricted",
    )
    await login(anon_client, basic_user)

    for path in (f"/kniha-jizd/vehicles/{hidden}/fuelings",
                 f"/kniha-jizd/vehicles/{hidden}/fuelings/new"):
        assert (await anon_client.get(path)).status_code == 404, path

    refused = await anon_client.post(
        f"/kniha-jizd/vehicles/{hidden}/fuelings/new",
        data={"csrf_token": "x", "fueled_at": date.today().isoformat(),
              "quantity": "40", "odometer_km": "1000"},
        follow_redirects=False,
    )
    assert refused.status_code in (403, 404)


async def test_private_vehicle_owner_can_record_a_fueling(logged_in_client, anon_client, basic_user):
    """Soukromé vozidlo je přesně ten případ, kdy nikdo nevede knihu jízd."""
    from tests.test_private_vehicles import create_private

    await login(anon_client, basic_user)
    vehicle_id = await create_private(anon_client, code="FT-40", plate="1FT 0040")

    assert (await _add(anon_client, vehicle_id, km=100200, quantity="45")).status_code == 303
    page = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings")
    assert page.status_code == 200

    # Cizí se k tomu nedostane.
    assert (await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings")).status_code == 200
