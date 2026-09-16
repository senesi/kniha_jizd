"""Etapa 3 - rezervace, kalendář, konflikt při výpůjčce (zadání 9/10/31)."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.modules.reservations.calendar import LOCAL_TZ
from tests.conftest import create_vehicle, extract_csrf_token, login


def _local(day_offset: int, hour: int) -> str:
    """Hodnota pro <input type="datetime-local">."""
    moment = datetime.now(LOCAL_TZ) + timedelta(days=day_offset)
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


async def _reservation_row(reservation_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleReservation

    async with async_session_factory() as db:
        return (await db.execute(
            select(VehicleReservation).where(VehicleReservation.id == uuid.UUID(reservation_id))
        )).scalar_one()


async def _trip_row(trip_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Trip

    async with async_session_factory() as db:
        return (await db.execute(select(Trip).where(Trip.id == uuid.UUID(trip_id)))).scalar_one()


async def _reserve(ac, csrf, vehicle_id, *, start, end, **fields):
    data = {
        "csrf_token": csrf, "vehicle_id": vehicle_id, "start_at": start, "end_at": end,
        "purpose": "montáž", **fields,
    }
    return await ac.post("/kniha-jizd/reservations/new", data=data, follow_redirects=False)


async def _reserve_ok(ac, csrf, vehicle_id, **kwargs) -> str:
    response = await _reserve(ac, csrf, vehicle_id, **kwargs)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/reservations/")[1].split("?")[0]


# --- vytvoření a změna ------------------------------------------------

async def test_create_reservation(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-01", license_plate="1RA 0001")
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, start=_local(1, 8), end=_local(1, 16),
    )

    reservation = await _reservation_row(reservation_id)
    assert reservation.status == "active"
    assert reservation.kind == "reservation"
    assert str(reservation.user_id) == admin_user[2]
    assert reservation.end_at > reservation.start_at
    assert reservation.purpose == "montáž"


async def test_end_before_start_is_rejected(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-02", license_plate="1RA 0002")
    response = await _reserve(
        logged_in_client, csrf_token, vehicle_id, start=_local(1, 16), end=_local(1, 8),
    )
    assert response.status_code == 400
    assert "později než její začátek" in response.text


async def test_overlapping_reservation_is_refused(logged_in_client, csrf_token):
    """Překryv hlídá databáze (EXCLUDE constraint), ne aplikace - tady se
    ověřuje, že se z toho stane srozumitelná hláška, ne pád."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-03", license_plate="1RA 0003")
    await _reserve_ok(logged_in_client, csrf_token, vehicle_id, start=_local(2, 8), end=_local(2, 16))

    overlapping = await _reserve(
        logged_in_client, csrf_token, vehicle_id, start=_local(2, 12), end=_local(2, 18),
    )
    assert overlapping.status_code == 400
    assert "už je vozidlo rezervované" in overlapping.text


async def test_touching_windows_do_not_overlap(logged_in_client, csrf_token):
    """Konec jedné a začátek druhé ve stejný okamžik se nepřekrývají -
    rozsah je polouzavřený."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-04", license_plate="1RA 0004")
    await _reserve_ok(logged_in_client, csrf_token, vehicle_id, start=_local(3, 8), end=_local(3, 12))
    second = await _reserve(logged_in_client, csrf_token, vehicle_id, start=_local(3, 12), end=_local(3, 16))
    assert second.status_code == 303


async def test_different_vehicles_may_share_a_window(logged_in_client, csrf_token):
    first = await create_vehicle(logged_in_client, csrf_token, internal_code="R-05", license_plate="1RA 0005")
    second = await create_vehicle(logged_in_client, csrf_token, internal_code="R-06", license_plate="1RA 0006")
    await _reserve_ok(logged_in_client, csrf_token, first, start=_local(4, 8), end=_local(4, 16))
    other = await _reserve(logged_in_client, csrf_token, second, start=_local(4, 8), end=_local(4, 16))
    assert other.status_code == 303


async def test_cancelled_reservation_frees_the_window(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-07", license_plate="1RA 0007")
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, start=_local(5, 8), end=_local(5, 16),
    )

    blocked = await _reserve(logged_in_client, csrf_token, vehicle_id, start=_local(5, 9), end=_local(5, 12))
    assert blocked.status_code == 400

    cancelled = await logged_in_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/cancel",
        data={"csrf_token": csrf_token, "reason": "nepojedu"}, follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert (await _reservation_row(reservation_id)).status == "cancelled"

    # Termín je hned volný pro někoho jiného.
    freed = await _reserve(logged_in_client, csrf_token, vehicle_id, start=_local(5, 9), end=_local(5, 12))
    assert freed.status_code == 303


async def test_reservation_can_be_moved(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-08", license_plate="1RA 0008")
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, start=_local(6, 8), end=_local(6, 12),
    )
    response = await logged_in_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/edit",
        data={"csrf_token": csrf_token, "start_at": _local(6, 13), "end_at": _local(6, 17), "purpose": "jinak"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    reservation = await _reservation_row(reservation_id)
    assert reservation.purpose == "jinak"
    assert reservation.start_at.astimezone(LOCAL_TZ).hour == 13


# --- oprávnění --------------------------------------------------------

async def test_user_cannot_touch_someone_elses_reservation(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-10", license_plate="1RB 0010")
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, start=_local(7, 8), end=_local(7, 16),
    )

    await login(anon_client, basic_user)
    # Vidět ji smí (kalendář je společný), měnit ne.
    assert (await anon_client.get(f"/kniha-jizd/reservations/{reservation_id}")).status_code == 200
    assert (await anon_client.get(f"/kniha-jizd/reservations/{reservation_id}/edit")).status_code == 403

    form = await anon_client.get("/kniha-jizd/reservations/new")
    other_csrf = extract_csrf_token(form.text)
    cancelled = await anon_client.post(
        f"/kniha-jizd/reservations/{reservation_id}/cancel",
        data={"csrf_token": other_csrf}, follow_redirects=False,
    )
    assert cancelled.status_code == 403
    assert (await _reservation_row(reservation_id)).status == "active"


async def test_driver_cannot_create_service_block(logged_in_client, csrf_token, anon_client, basic_user):
    """Servisní blok odstaví vozidlo všem - to nesmí umět běžný řidič."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-11", license_plate="1RB 0011")

    await login(anon_client, basic_user)
    form = await anon_client.get("/kniha-jizd/reservations/new")
    driver_csrf = extract_csrf_token(form.text)
    response = await _reserve(
        anon_client, driver_csrf, vehicle_id, start=_local(8, 8), end=_local(8, 16), kind="service",
    )
    assert response.status_code == 403


async def test_admin_service_block_has_no_owner(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-12", license_plate="1RB 0012")
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, start=_local(9, 8), end=_local(9, 16), kind="service",
    )
    reservation = await _reservation_row(reservation_id)
    assert reservation.kind == "service"
    assert reservation.user_id is None


# --- konflikt při výpůjčce (zadání 10) --------------------------------

async def test_trip_over_foreign_reservation_warns_then_proceeds(
    logged_in_client, csrf_token, anon_client, basic_user
):
    """Cizí rezervace výpůjčku NEBLOKUJE, ale musí být vidět, s kým a na
    kdy - a potvrzení musí zůstat v historii."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-20", license_plate="1RC 0020")

    # Řidič si zabere termín, do kterého spadá „teď".
    await login(anon_client, basic_user)
    form = await anon_client.get("/kniha-jizd/reservations/new")
    driver_csrf = extract_csrf_token(form.text)
    now = datetime.now(LOCAL_TZ)
    reservation_id = await _reserve_ok(
        anon_client, driver_csrf, vehicle_id,
        start=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        end=(now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
    )

    # Admin (někdo jiný) se pokusí vozidlo vzít.
    warned = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert warned.status_code == 200
    assert "rezervováno" in warned.text
    assert "Test driver" in warned.text          # jméno rezervujícího
    assert 'value="reservation"' in warned.text  # potvrzovací pole

    confirmed = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000", "confirm": "reservation"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    trip_id = confirmed.headers["location"].split("/trips/")[1].split("?")[0]

    trip = await _trip_row(trip_id)
    assert str(trip.reservation_conflict_id) == reservation_id
    assert trip.reservation_override_at is not None
    assert trip.reservation_id is None

    # Cizí rezervace zůstává nedotčená - nikdo ji nesmí přejezdem zrušit.
    assert (await _reservation_row(reservation_id)).status == "active"


async def test_trip_on_own_reservation_fulfils_it_without_warning(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-21", license_plate="1RC 0021")
    now = datetime.now(LOCAL_TZ)
    reservation_id = await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id,
        start=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        end=(now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
    )

    # Žádné varování - je to moje rezervace.
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert response.status_code == 303
    trip_id = response.headers["location"].split("/trips/")[1].split("?")[0]

    trip = await _trip_row(trip_id)
    assert str(trip.reservation_id) == reservation_id
    assert trip.reservation_conflict_id is None
    assert (await _reservation_row(reservation_id)).status == "fulfilled"


async def test_service_block_warns_before_trip(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-22", license_plate="1RC 0022")
    now = datetime.now(LOCAL_TZ)
    await _reserve_ok(
        logged_in_client, csrf_token, vehicle_id, kind="service",
        start=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        end=(now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
    )
    warned = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert warned.status_code == 200
    assert "servis / mimo provoz" in warned.text


async def test_future_reservation_does_not_warn(logged_in_client, csrf_token, anon_client, basic_user):
    """Varovat se má jen na termín, do kterého „teď" opravdu spadá."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-23", license_plate="1RC 0023")

    await login(anon_client, basic_user)
    form = await anon_client.get("/kniha-jizd/reservations/new")
    await _reserve_ok(
        anon_client, extract_csrf_token(form.text), vehicle_id, start=_local(10, 8), end=_local(10, 16),
    )

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert response.status_code == 303


# --- kalendář ---------------------------------------------------------

async def test_calendar_renders_and_shows_free_days(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-30", license_plate="1RD 0030")

    page = await logged_in_client.get("/kniha-jizd/reservations")
    assert page.status_code == 200
    assert "1RD 0030" in page.text
    assert "volné" in page.text          # volný termín je vidět na první pohled
    assert "Rezervováno" in page.text    # legenda

    await _reserve_ok(logged_in_client, csrf_token, vehicle_id, start=_local(1, 8), end=_local(1, 16))
    with_reservation = await logged_in_client.get("/kniha-jizd/reservations")
    assert "rezerv." in with_reservation.text


async def test_calendar_week_navigation(logged_in_client):
    page = await logged_in_client.get("/kniha-jizd/reservations?week=2026-09-14")
    assert page.status_code == 200
    assert "week=2026-09-07" in page.text   # předchozí
    assert "week=2026-09-21" in page.text   # další


async def test_calendar_survives_nonsense_week(logged_in_client):
    """Rozbitý parametr v URL nesmí shodit stránku."""
    assert (await logged_in_client.get("/kniha-jizd/reservations?week=nesmysl")).status_code == 200


async def test_my_reservations_lists_only_mine(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="R-31", license_plate="1RD 0031")
    await _reserve_ok(logged_in_client, csrf_token, vehicle_id, start=_local(11, 8), end=_local(11, 16))

    mine = await logged_in_client.get("/kniha-jizd/reservations/mine")
    assert "1RD 0031" in mine.text

    await login(anon_client, basic_user)
    theirs = await anon_client.get("/kniha-jizd/reservations/mine")
    assert "1RD 0031" not in theirs.text
