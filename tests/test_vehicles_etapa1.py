"""Etapa 1 - vozidla, odpovědné osoby, QR, oprávnění (zadání 31)."""
import uuid

from sqlalchemy import select

from tests.conftest import create_vehicle, extract_csrf_token, login


async def _vehicle_row(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id)))).scalar_one()


# --- vytvoření a úprava ------------------------------------------------

async def test_create_vehicle(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token)

    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.license_plate == "1AB 2345"
    assert vehicle.current_odometer_km == 100000
    assert vehicle.is_active is True
    # QR token vzniká automaticky a nesmí být odvozený od id ani SPZ.
    assert vehicle.qr_token
    assert vehicle.qr_token not in (str(vehicle.id), vehicle.license_plate)

    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert page.status_code == 200
    assert "1AB 2345" in page.text


async def test_duplicate_internal_code_is_rejected(logged_in_client, csrf_token):
    await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-DUP", license_plate="1AA 1111")
    response = await logged_in_client.post(
        "/kniha-jizd/vehicles/new",
        data={"csrf_token": csrf_token, "internal_code": "VOZ-DUP", "license_plate": "2BB 2222",
              "brand": "Ford", "model": "Transit", "vehicle_type": "dodavka", "status": "available"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "už existuje" in response.text


async def test_update_vehicle_and_deactivate(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-EDIT", license_plate="3CC 3333")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={"csrf_token": csrf_token, "internal_code": "VOZ-EDIT", "license_plate": "3CC 3333",
              "brand": "Škoda", "model": "Superb", "vehicle_type": "osobni", "status": "in_service"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.model == "Superb"
    assert vehicle.status == "in_service"
    # Chybějící checkbox "is_active" znamená deaktivaci.
    assert vehicle.is_active is False


async def test_edit_cannot_move_odometer(logged_in_client, csrf_token):
    """Úprava vozidla nesmí sahat na stav km - na to je jen administrativní
    oprava se zdůvodněním (zadání 5)."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="VOZ-KM", license_plate="4DD 4444",
        current_odometer_km="200000",
    )
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/edit",
        data={"csrf_token": csrf_token, "internal_code": "VOZ-KM", "license_plate": "4DD 4444",
              "brand": "Škoda", "model": "Octavia", "vehicle_type": "osobni", "status": "available",
              "is_active": "1", "current_odometer_km": "5"},
        follow_redirects=False,
    )
    vehicle = await _vehicle_row(vehicle_id)
    assert vehicle.current_odometer_km == 200000


async def test_odometer_correction_is_audited(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-OD", license_plate="5EE 5555")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/odometer",
        data={"csrf_token": csrf_token, "new_km": "99000", "reason": "překlep při zadání"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (await _vehicle_row(vehicle_id)).current_odometer_km == 99000

    async with async_session_factory() as db:
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.action == "odometer_correction", AuditLog.entity_id == vehicle_id)
        )).scalars().all()
    assert len(entries) == 1
    assert entries[0].before_data["current_odometer_km"] == 100000
    assert entries[0].after_data["reason"] == "překlep při zadání"


async def test_odometer_correction_requires_reason(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-NR", license_plate="6FF 6666")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/odometer",
        data={"csrf_token": csrf_token, "new_km": "50000", "reason": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert (await _vehicle_row(vehicle_id)).current_odometer_km == 100000


# --- odpovědná osoba ---------------------------------------------------

async def test_assigning_responsible_person_records_history(logged_in_client, csrf_token, responsible_user):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleAssignment

    _, _, responsible_id = responsible_user
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="VOZ-RESP", license_plate="7GG 7777",
        responsible_user_id=responsible_id,
    )

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(VehicleAssignment).where(VehicleAssignment.vehicle_id == uuid.UUID(vehicle_id))
        )).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].user_id) == responsible_id
    assert rows[0].valid_to is None


async def test_responsible_person_manages_only_own_vehicle(
    logged_in_client, csrf_token, anon_client, responsible_user
):
    _, _, responsible_id = responsible_user
    mine = await create_vehicle(
        logged_in_client, csrf_token, internal_code="VOZ-MINE", license_plate="8HH 8888",
        responsible_user_id=responsible_id,
    )
    other = await create_vehicle(
        logged_in_client, csrf_token, internal_code="VOZ-OTHER", license_plate="9II 9999",
    )

    await login(anon_client, responsible_user)
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{mine}/edit")).status_code == 200
    # Cizí vozidlo smí vidět, ale ne spravovat.
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{other}")).status_code == 200
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{other}/edit")).status_code == 403


async def test_driver_cannot_manage_vehicles(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-DRV", license_plate="1JJ 1111")

    await login(anon_client, basic_user)
    assert (await anon_client.get("/kniha-jizd/vehicles")).status_code == 200
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")).status_code == 200
    assert (await anon_client.get("/kniha-jizd/vehicles/new")).status_code == 403
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/edit")).status_code == 403
    assert (await anon_client.get("/kniha-jizd/users")).status_code == 403
    assert (await anon_client.get("/kniha-jizd/settings")).status_code == 403


# --- QR ----------------------------------------------------------------

async def test_qr_landing_opens_the_right_vehicle(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-QR", license_plate="2KK 2222")
    vehicle = await _vehicle_row(vehicle_id)

    response = await logged_in_client.get(f"/kniha-jizd/v/{vehicle.qr_token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/kniha-jizd/vehicles/{vehicle_id}"


async def test_qr_landing_rejects_unknown_token(logged_in_client):
    assert (await logged_in_client.get("/kniha-jizd/v/neexistuje")).status_code == 404


async def test_qr_landing_requires_login(anon_client, logged_in_client, csrf_token):
    """Token v QR kódu sám o sobě nic neodemyká - nepřihlášený uživatel se
    přesměruje na login a ten si zapamatuje, kam se má vrátit."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-ANON", license_plate="3LL 3333")
    vehicle = await _vehicle_row(vehicle_id)

    response = await anon_client.get(f"/kniha-jizd/v/{vehicle.qr_token}", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "/kniha-jizd/login" in response.headers["location"]
    assert f"next=/kniha-jizd/v/{vehicle.qr_token}" in response.headers["location"]


async def test_qr_image_requires_manage_permission(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-QRP", license_plate="4MM 4444")
    assert (await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/qr.png")).status_code == 200

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/qr")).status_code == 403


# --- CSRF a přihlášení --------------------------------------------------

async def test_post_without_csrf_is_rejected(logged_in_client):
    response = await logged_in_client.post(
        "/kniha-jizd/vehicles/new",
        data={"internal_code": "VOZ-NOCSRF", "license_plate": "5NN 5555", "brand": "X", "model": "Y"},
        follow_redirects=False,
    )
    assert response.status_code == 422  # chybí povinné pole csrf_token


async def test_post_with_wrong_csrf_is_rejected(logged_in_client):
    response = await logged_in_client.post(
        "/kniha-jizd/vehicles/new",
        data={"csrf_token": "podvrzeny-token", "internal_code": "VOZ-BADCSRF", "license_plate": "6OO 6666",
              "brand": "X", "model": "Y", "vehicle_type": "osobni", "status": "available"},
        follow_redirects=False,
    )
    assert response.status_code == 403


async def test_login_returns_to_requested_page(anon_client, admin_user):
    email, password, _ = admin_user
    form = await anon_client.get("/kniha-jizd/login?next=/kniha-jizd/vehicles")
    assert 'name="next" value="/kniha-jizd/vehicles"' in form.text

    response = await anon_client.post(
        "/kniha-jizd/login",
        data={"email": email, "password": password, "next": "/kniha-jizd/vehicles"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/kniha-jizd/vehicles"


async def test_login_ignores_external_next(anon_client, admin_user):
    """Formulář se nesmí dát zneužít jako otevřený redirect."""
    email, password, _ = admin_user
    response = await anon_client.post(
        "/kniha-jizd/login",
        data={"email": email, "password": password, "next": "https://example.com/phish"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/kniha-jizd/"


async def test_wrong_password_does_not_log_in(anon_client, admin_user):
    email, _, _ = admin_user
    response = await anon_client.post(
        "/kniha-jizd/login", data={"email": email, "password": "spatne-heslo"}, follow_redirects=False,
    )
    assert response.status_code == 401
    assert (await anon_client.get("/kniha-jizd/vehicles", follow_redirects=False)).status_code in (302, 303, 307)


# --- fotografie ---------------------------------------------------------

async def test_photo_upload_and_authorized_download(logged_in_client, csrf_token, anon_client, basic_user):
    import io

    from PIL import Image

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-FOTO", license_plate="7PP 7777")

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 900), (10, 116, 144)).save(buffer, format="JPEG")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/photos",
        data={"csrf_token": csrf_token},
        files={"photo": ("tachometr.jpg", buffer.getvalue(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    attachment_id = detail.text.split("/kniha-jizd/attachments/")[1].split("/")[0]

    assert (await logged_in_client.get(f"/kniha-jizd/attachments/{attachment_id}/thumb")).status_code == 200
    assert (await logged_in_client.get(f"/kniha-jizd/attachments/{attachment_id}/full")).status_code == 200

    # Nepřihlášený se k souboru nedostane ani se správným id.
    assert (await anon_client.get(
        f"/kniha-jizd/attachments/{attachment_id}/full", follow_redirects=False
    )).status_code in (302, 303, 307)


async def test_non_image_upload_is_rejected(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="VOZ-BAD", license_plate="8QQ 8888")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/photos",
        data={"csrf_token": csrf_token},
        files={"photo": ("smlouva.pdf", b"%PDF-1.4 nope", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 400
