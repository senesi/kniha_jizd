"""Etapa 2 - výpůjčky: start, konec, km, časy, řidiči, validace (zadání 31)."""
import io
import uuid

from PIL import Image
from sqlalchemy import select

from tests.conftest import browser_multipart, create_vehicle, extract_csrf_token, login

BOUNDARY_HEADERS = {"Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryTEST"}


# --- pomocné ----------------------------------------------------------

async def _trip_row(trip_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Trip

    async with async_session_factory() as db:
        return (await db.execute(select(Trip).where(Trip.id == uuid.UUID(trip_id)))).scalar_one()


async def _vehicle_row(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id)))).scalar_one()


def _photo_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), (20, 30, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _start(ac, csrf, vehicle_id, **fields):
    data = {"csrf_token": csrf, "start_odometer_km": "100000", "start_fuel_level": "50", **fields}
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start", data=data, follow_redirects=False)


async def _start_ok(ac, csrf, vehicle_id, **fields) -> str:
    response = await _start(ac, csrf, vehicle_id, **fields)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/trips/")[1].split("?")[0]


async def _end(ac, csrf, trip_id, **fields):
    data = {
        "csrf_token": csrf, "end_odometer_km": "100120", "end_fuel_level": "30",
        "purpose_code": "montaz", "route_text": "Mladá Boleslav – Zlatá Olešnice – Mladá Boleslav",
        **fields,
    }
    return await ac.post(f"/kniha-jizd/trips/{trip_id}/end", data=data, follow_redirects=False)


# --- zahájení ---------------------------------------------------------

async def test_start_trip_records_driver_time_and_state(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-01", license_plate="1AA 0001")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100050")

    trip = await _trip_row(trip_id)
    assert trip.status == "active"
    assert trip.start_odometer_km == 100050
    assert trip.start_fuel_level == 50
    assert trip.started_at is not None
    assert trip.ended_at is None
    assert str(trip.primary_driver_id) == admin_user[2]
    assert str(trip.started_by) == admin_user[2]

    # Stav vozidla se posouvá už při startu - je to novější údaj.
    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.current_odometer_km == 100050
    assert vehicle.current_fuel_level == 50
    assert vehicle.state_updated_at is not None


async def test_only_one_active_trip_per_vehicle(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-02", license_plate="1AA 0002")
    await _start_ok(logged_in_client, csrf_token, vehicle_id)

    second = await _start(logged_in_client, csrf_token, vehicle_id)
    assert second.status_code == 400
    assert "otevřenou výpůjčku" in second.text


async def test_start_cannot_lower_the_odometer(logged_in_client, csrf_token):
    """Zadání 5/32: běžná jízda nesmí stav km snížit."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="T-03", license_plate="1AA 0003",
        current_odometer_km="150000",
    )
    response = await _start(logged_in_client, csrf_token, vehicle_id, start_odometer_km="149000")
    assert response.status_code == 400
    assert "administrativně opravit" in response.text
    assert (await _vehicle_row(vehicle_id)).current_odometer_km == 150000


async def test_inactive_vehicle_cannot_be_borrowed(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-04", license_plate="1AA 0004")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={"csrf_token": csrf_token, "internal_code": "T-04", "license_plate": "1AA 0004",
              "brand": "Škoda", "model": "Octavia", "vehicle_type": "osobni", "status": "available"},
        follow_redirects=False,
    )
    response = await _start(logged_in_client, csrf_token, vehicle_id)
    assert response.status_code == 400
    assert "neaktivní" in response.text


async def test_vehicle_in_service_warns_then_proceeds_after_confirmation(logged_in_client, csrf_token):
    """Zadání 32: raději upozornit a nechat potvrdit než tvrdě blokovat -
    jízda do servisu je legitimní."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="T-05", license_plate="1AA 0005", status="in_service",
    )
    warned = await _start(logged_in_client, csrf_token, vehicle_id)
    assert warned.status_code == 200
    assert "v servisu" in warned.text
    assert 'value="vehicle_status"' in warned.text

    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, confirm="vehicle_status")
    assert (await _trip_row(trip_id)).status == "active"


async def test_start_confirmation_is_recorded_in_audit(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="T-06", license_plate="1AA 0006", status="blocked",
    )
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, confirm="vehicle_status")

    async with async_session_factory() as db:
        entry = (await db.execute(
            select(AuditLog).where(AuditLog.action == "trip_start", AuditLog.entity_id == trip_id)
        )).scalar_one()
    assert entry.after_data["confirmations"] == ["vehicle_status"]


# --- ukončení ---------------------------------------------------------

async def test_end_trip_computes_distance_and_closes(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-10", license_plate="1BB 0010")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100000")

    response = await _end(logged_in_client, csrf_token, trip_id, end_odometer_km="100276")
    assert response.status_code == 303

    trip = await _trip_row(trip_id)
    assert trip.status == "completed"
    assert trip.end_odometer_km == 100276
    assert trip.distance_km == 276
    assert trip.ended_at is not None
    assert trip.ended_at >= trip.started_at
    assert str(trip.ended_by) == admin_user[2]
    assert trip.purpose_code == "montaz"
    assert trip.route_text == "Mladá Boleslav – Zlatá Olešnice – Mladá Boleslav"
    assert trip.end_fuel_level == 30

    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.current_odometer_km == 100276


async def test_end_km_lower_than_start_is_rejected(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-11", license_plate="1BB 0011")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100000")

    response = await _end(logged_in_client, csrf_token, trip_id, end_odometer_km="99000")
    assert response.status_code == 400
    assert "nemůže být nižší" in response.text
    assert (await _trip_row(trip_id)).status == "active"


async def test_route_and_purpose_are_required(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-12", license_plate="1BB 0012")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)

    no_route = await _end(logged_in_client, csrf_token, trip_id, route_text="   ")
    assert no_route.status_code == 400
    assert "Trasa je povinná" in no_route.text

    no_purpose = await _end(logged_in_client, csrf_token, trip_id, purpose_code="")
    assert no_purpose.status_code == 400
    assert "účel jízdy" in no_purpose.text

    other_without_text = await _end(logged_in_client, csrf_token, trip_id, purpose_code="jine", purpose_text="")
    assert other_without_text.status_code == 400

    assert (await _trip_row(trip_id)).status == "active"


async def test_large_distance_warns_then_proceeds(logged_in_client, csrf_token):
    """Výchozí práh je 2000 km (nastavitelný v administraci)."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-13", license_plate="1BB 0013")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100000")

    warned = await _end(logged_in_client, csrf_token, trip_id, end_odometer_km="105000")
    assert warned.status_code == 200
    assert 'value="odometer_jump"' in warned.text
    assert (await _trip_row(trip_id)).status == "active"

    confirmed = await _end(
        logged_in_client, csrf_token, trip_id, end_odometer_km="105000", confirm="odometer_jump",
    )
    assert confirmed.status_code == 303
    trip = await _trip_row(trip_id)
    assert trip.status == "completed"
    assert trip.distance_km == 5000


async def test_completed_trip_cannot_be_ended_twice(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-14", license_plate="1BB 0014")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)
    assert (await _end(logged_in_client, csrf_token, trip_id)).status_code == 303

    again = await _end(logged_in_client, csrf_token, trip_id, end_odometer_km="100500")
    assert again.status_code in (303, 400)
    trip = await _trip_row(trip_id)
    assert trip.end_odometer_km == 100120  # původní hodnota se nepřepsala


# --- oprávnění --------------------------------------------------------

async def test_stranger_cannot_end_someone_elses_trip(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-20", license_plate="1CC 0020")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/trips/{trip_id}/end")).status_code == 403
    assert (await anon_client.get(f"/kniha-jizd/trips/{trip_id}")).status_code == 200  # číst smí


async def test_responsible_person_can_close_forgotten_trip(
    logged_in_client, csrf_token, anon_client, responsible_user
):
    """Zapomenutá výpůjčka se musí dát uzavřít správcem vozidla."""
    _, _, responsible_id = responsible_user
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="T-21", license_plate="1CC 0021",
        responsible_user_id=responsible_id,
    )
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, responsible_user)
    form = await anon_client.get(f"/kniha-jizd/trips/{trip_id}/end")
    assert form.status_code == 200
    response = await _end(anon_client, extract_csrf_token(form.text), trip_id)
    assert response.status_code == 303

    trip = await _trip_row(trip_id)
    assert trip.status == "completed"
    assert str(trip.ended_by) == responsible_id
    assert str(trip.primary_driver_id) != responsible_id  # řidič zůstal původní


async def test_driver_role_can_start_and_end_own_trip(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-22", license_plate="1CC 0022")

    await login(anon_client, basic_user)
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    assert form.status_code == 200
    driver_csrf = extract_csrf_token(form.text)

    trip_id = await _start_ok(anon_client, driver_csrf, vehicle_id)
    assert (await _end(anon_client, driver_csrf, trip_id)).status_code == 303
    assert (await _trip_row(trip_id)).status == "completed"


# --- další řidiči (zadání 12) -----------------------------------------

async def test_extra_drivers_are_tracked(logged_in_client, csrf_token, basic_user):
    from app.core.db import async_session_factory
    from app.models.fleet import TripDriver

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-30", license_plate="1DD 0030")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)
    _, _, other_id = basic_user

    added = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/drivers",
        data={"csrf_token": csrf_token, "driver_id": other_id}, follow_redirects=False,
    )
    assert added.status_code == 303

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(TripDriver).where(TripDriver.trip_id == uuid.UUID(trip_id))
        )).scalars().all()
    assert [str(row.user_id) for row in rows] == [other_id]

    removed = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/drivers/{other_id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert removed.status_code == 303

    async with async_session_factory() as db:
        remaining = (await db.execute(
            select(TripDriver).where(TripDriver.trip_id == uuid.UUID(trip_id))
        )).scalars().all()
    assert remaining == []


async def test_extra_driver_may_end_the_trip(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-31", license_plate="1DD 0031")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)
    _, _, other_id = basic_user
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/drivers",
        data={"csrf_token": csrf_token, "driver_id": other_id}, follow_redirects=False,
    )

    await login(anon_client, basic_user)
    form = await anon_client.get(f"/kniha-jizd/trips/{trip_id}/end")
    assert form.status_code == 200
    assert (await _end(anon_client, extract_csrf_token(form.text), trip_id)).status_code == 303


# --- zrušení (administrativní zásah) ----------------------------------

async def test_admin_can_cancel_trip_and_row_survives(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import TripNote

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-40", license_plate="1EE 0040")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/cancel",
        data={"csrf_token": csrf_token, "reason": "omylem zahájeno"}, follow_redirects=False,
    )
    assert response.status_code == 303

    trip = await _trip_row(trip_id)
    assert trip.status == "cancelled"
    assert trip.ended_at is not None

    async with async_session_factory() as db:
        notes = (await db.execute(
            select(TripNote).where(TripNote.trip_id == uuid.UUID(trip_id))
        )).scalars().all()
    assert any("omylem zahájeno" in note.text for note in notes)

    # Vozidlo je zase volné.
    assert (await _start(logged_in_client, csrf_token, vehicle_id)).status_code == 303


async def test_driver_cannot_cancel_trip(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-41", license_plate="1EE 0041")

    await login(anon_client, basic_user)
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    driver_csrf = extract_csrf_token(form.text)
    trip_id = await _start_ok(anon_client, driver_csrf, vehicle_id)

    response = await anon_client.post(
        f"/kniha-jizd/trips/{trip_id}/cancel",
        data={"csrf_token": driver_csrf, "reason": "nechci"}, follow_redirects=False,
    )
    assert response.status_code == 403
    assert (await _trip_row(trip_id)).status == "active"


# --- fotografie tachometru --------------------------------------------

async def test_odometer_photo_is_attached_to_the_trip(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-50", license_plate="1FF 0050")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000", "start_fuel_level": "50"},
        files={"odometer_photo": ("tachometr.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    trip_id = response.headers["location"].split("/trips/")[1].split("?")[0]

    async with async_session_factory() as db:
        photos = (await db.execute(
            select(Attachment).where(Attachment.trip_id == uuid.UUID(trip_id))
        )).scalars().all()
    assert len(photos) == 1
    assert photos[0].kind == "odometer_start"
    assert str(photos[0].vehicle_id) == vehicle_id


async def test_trip_works_without_a_photo_from_a_real_browser(logged_in_client, csrf_token):
    """Prohlížeč u nevyplněného <input type=file> part NEVYNECHÁ - pošle
    ho s prázdným filename. Nejčastější cesta v terénu (řidič fotku
    nepořídí) proto musí projít."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-51", license_plate="1FF 0051")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        content=browser_multipart(
            {"csrf_token": csrf_token, "start_odometer_km": "100000", "start_fuel_level": "50"},
            empty_file_field="odometer_photo",
        ),
        headers=BOUNDARY_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


async def test_non_image_odometer_photo_is_rejected(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-52", license_plate="1FF 0052")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"},
        files={"odometer_photo": ("doklad.pdf", b"%PDF-1.4 nope", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "nepodařilo uložit" in response.text


# --- OCR (zadání 8/11/25) ---------------------------------------------

async def test_ocr_disagreement_asks_before_saving(logged_in_client, csrf_token, monkeypatch):
    """OCR nikdy neuloží hodnotu samo. Když přečte něco jiného, než co
    člověk napsal, zeptá se - a fotka se přitom nesmí ztratit."""
    from app.core import ocr
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    async def fake_read(image_bytes):
        return ocr.OdometerReading(value=100999, raw_text="100999 km")

    monkeypatch.setattr(ocr, "read_odometer", fake_read)

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-60", license_plate="1GG 0060")
    asked = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000", "start_fuel_level": "50"},
        files={"odometer_photo": ("tachometr.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert asked.status_code == 200
    # Filtr `km` odděluje tisíce nezlomitelnou mezerou (U+00A0), aby se
    # "100 999 km" na mobilu nezalomilo doprostřed čísla.
    assert "100 999" in asked.text   # návrh z fotografie
    assert "100 000" in asked.text   # a vedle něj zadaná hodnota
    assert 'name="odometer_attachment_id"' in asked.text

    # Zatím se nic neuložilo - jízda nevznikla.
    from app.models.fleet import Trip
    async with async_session_factory() as db:
        trips = (await db.execute(
            select(Trip).where(Trip.vehicle_id == uuid.UUID(vehicle_id))
        )).scalars().all()
    assert trips == []

    attachment_id = asked.text.split('name="odometer_attachment_id" value="')[1].split('"')[0]

    # Uživatel trvá na své hodnotě.
    confirmed = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000", "start_fuel_level": "50",
              "confirm": "ocr", "odometer_attachment_id": attachment_id},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    trip_id = confirmed.headers["location"].split("/trips/")[1].split("?")[0]

    trip = await _trip_row(trip_id)
    assert trip.start_odometer_km == 100000  # hodnota uživatele, ne OCR

    # Fotka z prvního kroku se nenavázala dvakrát ani neztratila.
    async with async_session_factory() as db:
        photos = (await db.execute(
            select(Attachment).where(Attachment.trip_id == uuid.UUID(trip_id))
        )).scalars().all()
    assert len(photos) == 1
    assert str(photos[0].id) == attachment_id


async def test_ocr_agreement_saves_in_one_step(logged_in_client, csrf_token, monkeypatch):
    """Když OCR potvrdí to, co člověk napsal, není se na co ptát."""
    from app.core import ocr

    async def fake_read(image_bytes):
        return ocr.OdometerReading(value=100000, raw_text="100000")

    monkeypatch.setattr(ocr, "read_odometer", fake_read)

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-61", license_plate="1GG 0061")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"},
        files={"odometer_photo": ("tachometr.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def test_ocr_failure_never_blocks_the_trip(logged_in_client, csrf_token, monkeypatch):
    """Zadání 25: OCR nesmí být kritickou závislostí."""
    from app.core import ocr

    async def exploding_read(image_bytes):
        raise RuntimeError("OCR engine spadl")

    monkeypatch.setattr(ocr, "_read_with_provider", exploding_read)
    monkeypatch.setattr(ocr, "is_configured", lambda: True)

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-62", license_plate="1GG 0062")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"},
        files={"odometer_photo": ("tachometr.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def test_cross_vehicle_attachment_cannot_be_hijacked(logged_in_client, csrf_token):
    """Podvržené id fotky ve skrytém poli nesmí přivlastnit cizí přílohu."""
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    other_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-70", license_plate="1HH 0070")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{other_id}/photos",
        data={"csrf_token": csrf_token},
        files={"photo": ("cizi.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    async with async_session_factory() as db:
        foreign = (await db.execute(
            select(Attachment).where(Attachment.vehicle_id == uuid.UUID(other_id))
        )).scalar_one()

    target_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-71", license_plate="1HH 0071")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{target_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000",
              "odometer_attachment_id": str(foreign.id)},
        follow_redirects=False,
    )
    assert response.status_code == 500 or response.status_code == 400

    async with async_session_factory() as db:
        unchanged = (await db.execute(
            select(Attachment).where(Attachment.id == foreign.id)
        )).scalar_one()
    assert unchanged.trip_id is None


# --- přehledy ---------------------------------------------------------

async def test_my_trips_lists_own_and_shared_trips(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-80", license_plate="1II 0080")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)
    _, _, other_id = basic_user
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/drivers",
        data={"csrf_token": csrf_token, "driver_id": other_id}, follow_redirects=False,
    )

    mine = await logged_in_client.get("/kniha-jizd/trips/mine")
    assert mine.status_code == 200
    assert "1II 0080" in mine.text

    await login(anon_client, basic_user)
    theirs = await anon_client.get("/kniha-jizd/trips/mine")
    assert "1II 0080" in theirs.text  # vidí ji i jako další řidič


async def test_vehicle_card_shows_active_trip(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-81", license_plate="1II 0081")

    before = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "Zahájit výpůjčku" in before.text

    await _start_ok(logged_in_client, csrf_token, vehicle_id)
    after = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "Vozidlo je právě vypůjčené" in after.text


async def test_start_form_redirects_to_running_trip(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="T-82", license_plate="1II 0082")
    trip_id = await _start_ok(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.get(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start", follow_redirects=False
    )
    assert response.status_code == 303
    assert trip_id in response.headers["location"]
