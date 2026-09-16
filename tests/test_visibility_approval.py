"""Požadavky B (schvalování), C (náhledy) a D (viditelnost vozidla)."""
import io
import uuid

from PIL import Image
from sqlalchemy import select

from tests.conftest import create_vehicle, extract_csrf_token, login


def _photo_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), (10, 116, 144)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _vehicle_row(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id)))).scalar_one()


async def _request_row(request_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import TripRequest

    async with async_session_factory() as db:
        return (await db.execute(
            select(TripRequest).where(TripRequest.id == uuid.UUID(request_id))
        )).scalar_one()


# ======================================================================
# D) Viditelnost vozidla
# ======================================================================

async def test_restricted_vehicle_is_invisible_to_driver(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user
):
    _, _, responsible_id = responsible_user
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-HID", license_plate="1SK 0001",
        visibility="restricted", responsible_user_id=responsible_id,
    )
    visible = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-VIS", license_plate="1SK 0002",
    )

    await login(anon_client, basic_user)

    listing = await anon_client.get("/kniha-jizd/vehicles")
    assert "1SK 0002" in listing.text
    assert "1SK 0001" not in listing.text

    # Přímý odkaz je 404, ne 403 - existence skrytého vozidla je sama o
    # sobě informace.
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}")).status_code == 404
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{visible}")).status_code == 200


async def test_qr_code_cannot_bypass_visibility(logged_in_client, csrf_token, anon_client, basic_user):
    """Nálepka na autě nesmí být obchvatem oprávnění (požadavek D)."""
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-QR", license_plate="1SK 0003", visibility="restricted",
    )
    vehicle = await _vehicle_row(hidden)

    await login(anon_client, basic_user)
    response = await anon_client.get(f"/kniha-jizd/v/{vehicle.qr_token}", follow_redirects=False)
    assert response.status_code == 404

    # Admin přes tutéž nálepku projde.
    assert (await logged_in_client.get(
        f"/kniha-jizd/v/{vehicle.qr_token}", follow_redirects=False
    )).status_code == 303


async def test_responsible_person_sees_own_restricted_vehicle(
    logged_in_client, csrf_token, anon_client, responsible_user
):
    _, _, responsible_id = responsible_user
    mine = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-MINE", license_plate="1SK 0004",
        visibility="restricted", responsible_user_id=responsible_id,
    )
    other = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-OTHER", license_plate="1SK 0005",
        visibility="restricted",
    )

    await login(anon_client, responsible_user)
    listing = await anon_client.get("/kniha-jizd/vehicles")
    assert "1SK 0004" in listing.text
    assert "1SK 0005" not in listing.text
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{mine}")).status_code == 200
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{other}")).status_code == 404


async def test_restricted_vehicle_hidden_in_dashboard_and_calendar(
    logged_in_client, csrf_token, anon_client, basic_user
):
    await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-DASH", license_plate="1SK 0006",
        visibility="restricted", stk_valid_until="2020-01-01",  # propadlé STK -> patřilo by na dashboard
    )

    await login(anon_client, basic_user)
    assert "1SK 0006" not in (await anon_client.get("/kniha-jizd/")).text
    assert "1SK 0006" not in (await anon_client.get("/kniha-jizd/reservations")).text
    assert "1SK 0006" not in (await anon_client.get("/kniha-jizd/reservations/new")).text

    # Administrátor ho naopak vidí všude.
    assert "1SK 0006" in (await logged_in_client.get("/kniha-jizd/reservations")).text


async def test_driver_cannot_start_trip_on_restricted_vehicle(
    logged_in_client, csrf_token, anon_client, basic_user
):
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-TRIP", license_plate="1SK 0007", visibility="restricted",
    )
    await login(anon_client, basic_user)
    form = await anon_client.get("/kniha-jizd/vehicles")
    driver_csrf = extract_csrf_token(form.text) if 'csrf_token' in form.text else csrf_token

    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}/trips/start")).status_code == 404


async def test_visibility_defaults_to_all(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="V-DEF", license_plate="1SK 0008")
    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.visibility == "all"
    assert vehicle.approval_required is False


# ======================================================================
# C) Náhledová fotografie
# ======================================================================

async def test_vehicle_list_shows_placeholder_without_photo(logged_in_client, csrf_token):
    await create_vehicle(logged_in_client, csrf_token, internal_code="V-NOPIC", license_plate="1FO 0001")
    listing = await logged_in_client.get("/kniha-jizd/vehicles")
    assert listing.status_code == 200
    # Neutrální silueta, ne rozbitý obrázek.
    assert "Vozidlo bez fotografie" in listing.text


async def test_vehicle_list_shows_thumbnail_when_photo_exists(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="V-PIC", license_plate="1FO 0002")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/photos",
        data={"csrf_token": csrf_token},
        files={"photo": ("auto.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    listing = await logged_in_client.get("/kniha-jizd/vehicles")
    assert "/thumb" in listing.text

    # A totéž na dashboardu i v kalendáři - jeden náhled na všech místech.
    assert "/thumb" in (await logged_in_client.get("/kniha-jizd/reservations")).text


# ======================================================================
# B) Schvalování vozidel
# ======================================================================

async def test_driver_cannot_start_trip_on_approval_vehicle(
    logged_in_client, csrf_token, anon_client, basic_user
):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-APR", license_plate="1AP 0001", approval_required="1",
    )
    assert (await _vehicle_row(vehicle_id)).approval_required is True

    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "vyžaduje schválení" in card.text
    driver_csrf = extract_csrf_token(card.text)

    blocked = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": driver_csrf, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert "approval_needed" in blocked.headers["location"]

    from app.core.db import async_session_factory
    from app.models.fleet import Trip
    async with async_session_factory() as db:
        trips = (await db.execute(select(Trip).where(Trip.vehicle_id == uuid.UUID(vehicle_id)))).scalars().all()
    assert trips == []


async def test_full_approval_flow(logged_in_client, csrf_token, anon_client, basic_user, admin_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-FLOW", license_plate="1AP 0002", approval_required="1",
    )

    # 1) řidič požádá
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    driver_csrf = extract_csrf_token(card.text)
    requested = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "montáž Zlatá Olešnice"}, follow_redirects=False,
    )
    assert requested.status_code == 303
    request_id = requested.headers["location"].split("/approvals/")[1].split("?")[0]
    assert (await _request_row(request_id)).status == "pending"

    # 2) řidič vidí stav své žádosti
    mine = await anon_client.get("/kniha-jizd/approvals/mine")
    assert "Čeká na schválení" in mine.text

    # 3) řidič o ní nesmí rozhodnout sám
    self_decide = await anon_client.post(
        f"/kniha-jizd/approvals/{request_id}/decide",
        data={"csrf_token": driver_csrf, "decision": "approve"}, follow_redirects=False,
    )
    assert self_decide.status_code == 403

    # 4) admin ji vidí v seznamu a schválí
    pending = await logged_in_client.get("/kniha-jizd/approvals/pending")
    assert "1AP 0002" in pending.text
    approved = await logged_in_client.post(
        f"/kniha-jizd/approvals/{request_id}/decide",
        data={"csrf_token": csrf_token, "decision": "approve", "note": "ok, jeďte"}, follow_redirects=False,
    )
    assert approved.status_code == 303

    row = await _request_row(request_id)
    assert row.status == "approved"
    assert row.valid_until is not None
    assert str(row.decided_by) == admin_user[2]

    # 5) řidič teď smí vyjet a jízda si schválení zapamatuje
    started = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": driver_csrf, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert started.status_code == 303
    trip_id = started.headers["location"].split("/trips/")[1].split("?")[0]

    from app.core.db import async_session_factory
    from app.models.fleet import Trip
    async with async_session_factory() as db:
        trip = (await db.execute(select(Trip).where(Trip.id == uuid.UUID(trip_id)))).scalar_one()
    assert str(trip.request_id) == request_id


async def test_approval_cannot_be_used_twice(logged_in_client, csrf_token, anon_client, basic_user):
    """Jedno schválení = nejvýše jedna jízda."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-ONCE", license_plate="1AP 0003", approval_required="1",
    )
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    driver_csrf = extract_csrf_token(card.text)

    requested = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "první"}, follow_redirects=False,
    )
    request_id = requested.headers["location"].split("/approvals/")[1].split("?")[0]
    await logged_in_client.post(
        f"/kniha-jizd/approvals/{request_id}/decide",
        data={"csrf_token": csrf_token, "decision": "approve"}, follow_redirects=False,
    )

    first = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": driver_csrf, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    trip_id = first.headers["location"].split("/trips/")[1].split("?")[0]
    await anon_client.post(
        f"/kniha-jizd/trips/{trip_id}/end",
        data={"csrf_token": driver_csrf, "end_odometer_km": "100100", "purpose_code": "montaz",
              "route_text": "tam a zpět"},
        follow_redirects=False,
    )

    # Druhý pokus na totéž schválení už neprojde.
    second = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": driver_csrf, "start_odometer_km": "100100"}, follow_redirects=False,
    )
    assert second.status_code == 303
    assert "approval_needed" in second.headers["location"]


async def test_duplicate_pending_request_is_refused(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-DUP", license_plate="1AP 0004", approval_required="1",
    )
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    driver_csrf = extract_csrf_token(card.text)

    first = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "a"}, follow_redirects=False,
    )
    assert first.status_code == 303

    second = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "b"}, follow_redirects=False,
    )
    assert second.status_code == 400
    assert "čekající žádost" in second.text


async def test_rejection_requires_a_reason(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-REJ", license_plate="1AP 0005", approval_required="1",
    )
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    driver_csrf = extract_csrf_token(card.text)
    requested = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "x"}, follow_redirects=False,
    )
    request_id = requested.headers["location"].split("/approvals/")[1].split("?")[0]

    without_reason = await logged_in_client.post(
        f"/kniha-jizd/approvals/{request_id}/decide",
        data={"csrf_token": csrf_token, "decision": "reject"}, follow_redirects=False,
    )
    assert without_reason.status_code == 400
    assert (await _request_row(request_id)).status == "pending"

    with_reason = await logged_in_client.post(
        f"/kniha-jizd/approvals/{request_id}/decide",
        data={"csrf_token": csrf_token, "decision": "reject", "note": "vozidlo je potřeba jinde"},
        follow_redirects=False,
    )
    assert with_reason.status_code == 303
    row = await _request_row(request_id)
    assert row.status == "rejected"
    assert row.valid_until is None


async def test_responsible_person_starts_approval_vehicle_directly(
    logged_in_client, csrf_token, anon_client, responsible_user
):
    """Kdo o vozidle rozhoduje, ten o něj nežádá."""
    _, _, responsible_id = responsible_user
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-RESP", license_plate="1AP 0006",
        approval_required="1", responsible_user_id=responsible_id,
    )
    await login(anon_client, responsible_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "Zahájit výpůjčku" in card.text

    started = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": extract_csrf_token(card.text), "start_odometer_km": "100000"},
        follow_redirects=False,
    )
    assert started.status_code == 303


async def test_requester_can_withdraw_own_request(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-CANC", license_plate="1AP 0007", approval_required="1",
    )
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    driver_csrf = extract_csrf_token(card.text)
    requested = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "x"}, follow_redirects=False,
    )
    request_id = requested.headers["location"].split("/approvals/")[1].split("?")[0]

    cancelled = await anon_client.post(
        f"/kniha-jizd/approvals/{request_id}/cancel",
        data={"csrf_token": driver_csrf}, follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert (await _request_row(request_id)).status == "cancelled"

    # Stažení uvolní cestu k nové žádosti.
    again = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": driver_csrf, "purpose": "podruhé"}, follow_redirects=False,
    )
    assert again.status_code == 303


async def test_stranger_cannot_read_someone_elses_request(
    logged_in_client, csrf_token, anon_client, basic_user, responsible_user
):
    _, _, responsible_id = responsible_user
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="V-PRIV", license_plate="1AP 0008",
        approval_required="1", responsible_user_id=responsible_id,
    )
    await login(anon_client, basic_user)
    card = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    requested = await anon_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/request",
        data={"csrf_token": extract_csrf_token(card.text), "purpose": "x"}, follow_redirects=False,
    )
    request_id = requested.headers["location"].split("/approvals/")[1].split("?")[0]

    # Jiný řidič, který s vozidlem nemá nic společného.
    from tests.conftest import _make_user
    stranger = await _make_user("user", "stranger")
    from app.main import app
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as third:
        await login(third, stranger)
        assert (await third.get(f"/kniha-jizd/approvals/{request_id}")).status_code == 404
