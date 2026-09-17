"""Kola/pneumatiky a výdaje vozidla."""
import io
import uuid
from datetime import date, timedelta

from PIL import Image
from sqlalchemy import select

from tests.conftest import create_vehicle, extract_csrf_token, login

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), (60, 60, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _sets(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import WheelSet

    async with async_session_factory() as db:
        return list((await db.execute(
            select(WheelSet).where(WheelSet.vehicle_id == uuid.UUID(vehicle_id))
            .order_by(WheelSet.created_at)
        )).scalars().all())


async def _fitments(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import WheelFitment

    async with async_session_factory() as db:
        return list((await db.execute(
            select(WheelFitment).where(WheelFitment.vehicle_id == uuid.UUID(vehicle_id))
            .order_by(WheelFitment.created_at)
        )).scalars().all())


async def _expenses(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleExpense

    async with async_session_factory() as db:
        return list((await db.execute(
            select(VehicleExpense).where(VehicleExpense.vehicle_id == uuid.UUID(vehicle_id))
            .order_by(VehicleExpense.created_at)
        )).scalars().all())


async def _audit(action: str, entity_id: str):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    async with async_session_factory() as db:
        return list((await db.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == entity_id)
        )).scalars().all())


async def _add_set(ac, csrf, vehicle_id, **fields):
    data = {"csrf_token": csrf, "season": "winter", "brand": "Continental", "size": "205/55 R16", **fields}
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/wheels/sets", data=data, follow_redirects=False)


async def _fit(ac, csrf, vehicle_id, set_id, **fields):
    data = {
        "csrf_token": csrf, "wheel_set_id": str(set_id),
        "fitted_at": date.today().isoformat(), "odometer_km": "100000", **fields,
    }
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/wheels/fit", data=data, follow_redirects=False)


async def _add_expense(ac, csrf, vehicle_id, **fields):
    data = {
        "csrf_token": csrf, "expense_date": date.today().isoformat(),
        "expense_type": "myti", "amount_czk": "250", "currency": "CZK", **fields,
    }
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new", data=data, follow_redirects=False)


async def _start_trip(ac, csrf, vehicle_id, **fields) -> str:
    data = {"csrf_token": csrf, "start_odometer_km": "100000", **fields}
    response = await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/trips/")[1].split("?")[0]


# ======================================================================
# Sady kol
# ======================================================================

async def test_create_wheel_set(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-01", license_plate="1KO 0001")

    response = await _add_set(
        logged_in_client, csrf_token, vehicle_id,
        model="WinterContact TS 870", purchased_at="2024-10-01",
        purchase_odometer_km="80000", dot_code="2124", tread_depth_mm="8,0", note="komplet s disky",
    )
    assert response.status_code == 303

    sets = await _sets(vehicle_id)
    assert len(sets) == 1
    wheel_set = sets[0]
    assert wheel_set.season == "winter"
    assert wheel_set.brand == "Continental"
    assert wheel_set.size == "205/55 R16"
    assert wheel_set.dot_code == "2124"
    assert float(wheel_set.tread_depth_mm) == 8.0
    assert str(wheel_set.created_by) == admin_user[2]
    assert await _audit("create", str(wheel_set.id))


async def test_wheel_set_validation(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-02", license_plate="1KO 0002")

    bad_season = await _add_set(logged_in_client, csrf_token, vehicle_id, season="jarni")
    assert bad_season.status_code == 400

    future = await _add_set(
        logged_in_client, csrf_token, vehicle_id,
        purchased_at=(date.today() + timedelta(days=1)).isoformat(),
    )
    assert future.status_code == 400

    bad_tread = await _add_set(logged_in_client, csrf_token, vehicle_id, tread_depth_mm="99")
    assert bad_tread.status_code == 400

    assert await _sets(vehicle_id) == []


async def test_wheel_set_photo(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-03", license_plate="1KO 0003")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/wheels/sets",
        data={"csrf_token": csrf_token, "season": "summer", "brand": "Michelin"},
        files={"photo": ("kola.jpg", _image_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    wheel_set = (await _sets(vehicle_id))[0]
    async with async_session_factory() as db:
        photos = (await db.execute(
            select(Attachment).where(Attachment.wheel_set_id == wheel_set.id)
        )).scalars().all()
    assert len(photos) == 1
    assert photos[0].kind == "wheel_photo"


# ======================================================================
# Přezutí
# ======================================================================

async def test_fit_and_remove(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="W-10", license_plate="1KP 0010",
        current_odometer_km="100000",
    )
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter")
    wheel_set = (await _sets(vehicle_id))[0]

    fitted = await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id, odometer_km="100000")
    assert fitted.status_code == 303

    fitments = await _fitments(vehicle_id)
    assert len(fitments) == 1
    assert fitments[0].removed_at is None
    assert fitments[0].fitted_odometer_km == 100000
    assert await _audit("fit", str(fitments[0].id))

    removed = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/wheels/remove",
        data={"csrf_token": csrf_token, "removed_at": date.today().isoformat(), "odometer_km": "115400"},
        follow_redirects=False,
    )
    assert removed.status_code == 303

    fitments = await _fitments(vehicle_id)
    assert fitments[0].removed_at == date.today()
    assert fitments[0].removed_odometer_km == 115400
    assert await _audit("remove", str(fitments[0].id))


async def test_changing_wheels_closes_previous_set(logged_in_client, csrf_token):
    """Přezutí je jeden krok: stará sada se sundá, nová nasadí."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="W-11", license_plate="1KP 0011",
        current_odometer_km="100000",
    )
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter", brand="Continental")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="summer", brand="Michelin")
    winter, summer = await _sets(vehicle_id)

    await _fit(logged_in_client, csrf_token, vehicle_id, winter.id, odometer_km="100000")
    await _fit(logged_in_client, csrf_token, vehicle_id, summer.id, odometer_km="115400")

    fitments = await _fitments(vehicle_id)
    assert len(fitments) == 2

    closed = [f for f in fitments if f.wheel_set_id == winter.id][0]
    active = [f for f in fitments if f.wheel_set_id == summer.id][0]
    assert closed.removed_at == date.today()
    assert closed.removed_odometer_km == 115400
    assert active.removed_at is None


async def test_two_active_sets_are_impossible(logged_in_client, csrf_token):
    """Jedno vozidlo nemůže mít dvě nasazené sady - hlídá to částečný
    unikátní index, ne jen aplikace."""
    from app.core.db import async_session_factory
    from app.models.fleet import WheelFitment
    from sqlalchemy.exc import IntegrityError

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-12", license_plate="1KP 0012")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="summer")
    winter, summer = await _sets(vehicle_id)
    await _fit(logged_in_client, csrf_token, vehicle_id, winter.id)

    # Obejít aplikaci a zapsat druhý otevřený řádek přímo - databáze
    # to musí odmítnout.
    failed = False
    async with async_session_factory() as db:
        db.add(WheelFitment(
            wheel_set_id=summer.id, vehicle_id=uuid.UUID(vehicle_id),
            fitted_at=date.today(), fitted_odometer_km=100000,
        ))
        try:
            await db.commit()
        except IntegrityError:
            failed = True
    assert failed, "databáze povolila dvě nasazené sady"


async def test_same_set_cannot_be_fitted_twice(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-13", license_plate="1KP 0013")
    await _add_set(logged_in_client, csrf_token, vehicle_id)
    wheel_set = (await _sets(vehicle_id))[0]
    await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id)

    again = await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id)
    assert again.status_code == 400
    assert "už je na vozidle nasazená" in again.text


async def test_distance_calculation(logged_in_client, csrf_token):
    """Nasazená sada se počítá proti aktuálnímu stavu vozidla, sundaná
    proti stavu při sundání."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="W-14", license_plate="1KP 0014",
        current_odometer_km="100000",
    )
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="summer")
    winter, summer = await _sets(vehicle_id)

    await _fit(logged_in_client, csrf_token, vehicle_id, winter.id, odometer_km="100000")
    await _fit(logged_in_client, csrf_token, vehicle_id, summer.id, odometer_km="115400")

    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/wheels")
    assert page.status_code == 200
    # Zimní: 115400 - 100000 = 15 400 km (sundaná)
    assert "15 400 km" in page.text

    # Nasazená letní se počítá proti aktuálnímu stavu vozidla. Ten se
    # posune uzavřenou jízdou.
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id, start_odometer_km="115400")
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/end",
        data={"csrf_token": csrf_token, "end_odometer_km": "116000", "purpose_code": "montaz",
              "route_text": "tam a zpět"},
        follow_redirects=False,
    )
    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/wheels")
    assert "600 km" in page.text


async def test_fitting_below_previous_km_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="W-15", license_plate="1KP 0015",
        current_odometer_km="100000",
    )
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="summer")
    winter, summer = await _sets(vehicle_id)
    await _fit(logged_in_client, csrf_token, vehicle_id, winter.id, odometer_km="100000")

    response = await _fit(
        logged_in_client, csrf_token, vehicle_id, summer.id,
        odometer_km="90000", confirm="odometer_lower",
    )
    assert response.status_code == 400
    assert "nižší než při nasazení předchozí sady" in response.text


async def test_low_tread_warns_then_proceeds(logged_in_client, csrf_token):
    """Dezén pod zákonným minimem je důvod k upozornění, ne k zákazu."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-16", license_plate="1KP 0016")
    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter", tread_depth_mm="3,0")
    wheel_set = (await _sets(vehicle_id))[0]

    warned = await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id)
    assert warned.status_code == 200
    assert "pod zákonným minimem" in warned.text
    assert 'value="low_tread"' in warned.text
    assert await _fitments(vehicle_id) == []

    confirmed = await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id, confirm="low_tread")
    assert confirmed.status_code == 303


async def test_fitted_set_cannot_be_deleted(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-17", license_plate="1KP 0017")
    await _add_set(logged_in_client, csrf_token, vehicle_id)
    wheel_set = (await _sets(vehicle_id))[0]
    await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id)

    response = await logged_in_client.post(
        f"/kniha-jizd/wheel-sets/{wheel_set.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert response.status_code == 400
    assert (await _sets(vehicle_id))[0].deleted_at is None


async def test_vehicle_card_shows_current_wheels(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-20", license_plate="1KQ 0020")

    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "Kola nejsou evidovaná" in card.text

    await _add_set(logged_in_client, csrf_token, vehicle_id, season="winter", brand="Continental")
    wheel_set = (await _sets(vehicle_id))[0]
    await _fit(logged_in_client, csrf_token, vehicle_id, wheel_set.id)

    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "ZIMNÍ KOLA" in card.text
    assert "Continental" in card.text


async def test_wheels_respect_visibility(logged_in_client, csrf_token, anon_client, basic_user):
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="W-30", license_plate="1KR 0030", visibility="restricted",
    )
    await _add_set(logged_in_client, csrf_token, hidden, brand="TajnáZnačka")
    wheel_set = (await _sets(hidden))[0]

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}/wheels")).status_code == 404

    form = await anon_client.get("/kniha-jizd/vehicles")
    # Řidič nesmí sadu ani upravit přes přímé ID
    deleted = await anon_client.post(
        f"/kniha-jizd/wheel-sets/{wheel_set.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert deleted.status_code in (403, 404)


async def test_driver_cannot_manage_wheels(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="W-31", license_plate="1KR 0031")
    await _add_set(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, basic_user)
    page = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/wheels")
    assert page.status_code == 200          # vidět smí
    assert "Přezout vozidlo" not in page.text

    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    driver_csrf = extract_csrf_token(form.text)
    assert (await _add_set(anon_client, driver_csrf, vehicle_id)).status_code == 403


# ======================================================================
# Výdaje
# ======================================================================

async def test_create_expense(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-01", license_plate="1VY 0001")

    response = await _add_expense(
        logged_in_client, csrf_token, vehicle_id,
        expense_type="stk", amount_czk="1 450,50", supplier="STK Mladá Boleslav", odometer_km="100000",
    )
    assert response.status_code == 303

    expenses = await _expenses(vehicle_id)
    assert len(expenses) == 1
    expense = expenses[0]
    assert expense.expense_type == "stk"
    assert float(expense.amount_czk) == 1450.50
    assert expense.currency == "CZK"
    assert expense.supplier == "STK Mladá Boleslav"
    assert expense.odometer_km == 100000
    assert str(expense.created_by) == admin_user[2]
    assert await _audit("create", str(expense.id))


async def test_expense_validation(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-02", license_plate="1VY 0002")

    no_amount = await _add_expense(logged_in_client, csrf_token, vehicle_id, amount_czk="")
    assert no_amount.status_code == 400
    assert "Částka je povinná" in no_amount.text

    negative = await _add_expense(logged_in_client, csrf_token, vehicle_id, amount_czk="-50")
    assert negative.status_code == 400

    bad_type = await _add_expense(logged_in_client, csrf_token, vehicle_id, expense_type="vymyslene")
    assert bad_type.status_code == 400

    future = await _add_expense(
        logged_in_client, csrf_token, vehicle_id,
        expense_date=(date.today() + timedelta(days=1)).isoformat(),
    )
    assert future.status_code == 400

    assert await _expenses(vehicle_id) == []


async def test_large_amount_warns_then_proceeds(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-03", license_plate="1VY 0003")

    warned = await _add_expense(logged_in_client, csrf_token, vehicle_id, amount_czk="450000")
    assert warned.status_code == 200
    assert 'value="large_amount"' in warned.text
    assert await _expenses(vehicle_id) == []

    confirmed = await _add_expense(
        logged_in_client, csrf_token, vehicle_id, amount_czk="450000", confirm="large_amount",
    )
    assert confirmed.status_code == 303


async def test_vat_split_mismatch_warns(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-04", license_plate="1VY 0004")

    warned = await _add_expense(
        logged_in_client, csrf_token, vehicle_id,
        amount_czk="1000", amount_net_czk="500", vat_czk="105",
    )
    assert warned.status_code == 200
    assert "Zkontrolujte hodnoty" in warned.text

    # Sedící rozpad projde bez ptaní
    ok = await _add_expense(
        logged_in_client, csrf_token, vehicle_id,
        amount_czk="1000", amount_net_czk="826,45", vat_czk="173,55",
    )
    assert ok.status_code == 303


async def test_expense_receipt_pdf(logged_in_client, csrf_token):
    """Doklad může být PDF - používá se stejné úložiště jako u dokumentů
    vozidla, které PDF umí."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-10", license_plate="1VZ 0010")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new",
        data={"csrf_token": csrf_token, "expense_date": date.today().isoformat(),
              "expense_type": "servis", "amount_czk": "3500", "currency": "CZK"},
        files={"receipt": ("faktura.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    expense = (await _expenses(vehicle_id))[0]
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleDocument
    async with async_session_factory() as db:
        receipts = (await db.execute(
            select(VehicleDocument).where(VehicleDocument.expense_id == expense.id)
        )).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].mime_type == "application/pdf"

    # A jde stáhnout
    download = await logged_in_client.get(f"/kniha-jizd/documents/{receipts[0].id}/file")
    assert download.status_code == 200
    assert download.content == MINIMAL_PDF


async def test_expense_receipt_is_not_in_vehicle_documents(logged_in_client, csrf_token):
    """Účtenky se nesmí míchat mezi TP a zelenou kartu."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-11", license_plate="1VZ 0011")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new",
        data={"csrf_token": csrf_token, "expense_date": date.today().isoformat(),
              "expense_type": "myti", "amount_czk": "250", "currency": "CZK"},
        files={"receipt": ("uctenka.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )

    documents = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/documents")
    assert "uctenka.pdf" not in documents.text
    assert "Zatím žádné dokumenty" in documents.text

    expenses = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses")
    assert "uctenka.pdf" in expenses.text


async def test_expense_linked_to_trip(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-12", license_plate="1VZ 0012")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await _add_expense(
        logged_in_client, csrf_token, vehicle_id, expense_type="myti", trip_id=trip_id,
    )
    assert response.status_code == 303
    assert str((await _expenses(vehicle_id))[0].trip_id) == trip_id


async def test_expense_cannot_link_foreign_trip(logged_in_client, csrf_token):
    first = await create_vehicle(logged_in_client, csrf_token, internal_code="E-13", license_plate="1VZ 0013")
    second = await create_vehicle(logged_in_client, csrf_token, internal_code="E-14", license_plate="1VZ 0014")
    trip_id = await _start_trip(logged_in_client, csrf_token, first)

    response = await _add_expense(logged_in_client, csrf_token, second, trip_id=trip_id)
    assert response.status_code == 400
    assert "jinému vozidlu" in response.text


async def test_totals_include_fuelings_without_duplication(logged_in_client, csrf_token):
    """Tankování se do výdajů nepřepisuje, ale do celkových nákladů
    patří - jinak by přehled lhal."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-20", license_plate="1WA 0020")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": date.today().isoformat(),
              "quantity": "50", "unit": "l", "price_total_czk": "1950"},
        follow_redirects=False,
    )
    await _add_expense(logged_in_client, csrf_token, vehicle_id, expense_type="myti", amount_czk="250")

    page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses")
    assert page.status_code == 200
    assert "1 950,00" in page.text      # z jízdy
    assert "250,00" in page.text             # zapsaný výdaj
    assert "2 200,00" in page.text      # celkem

    # A tankování se nezdvojilo do tabulky výdajů
    assert len(await _expenses(vehicle_id)) == 1


async def test_expense_soft_delete(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-21", license_plate="1WA 0021")
    await _add_expense(logged_in_client, csrf_token, vehicle_id)
    expense = (await _expenses(vehicle_id))[0]

    response = await logged_in_client.post(
        f"/kniha-jizd/expenses/{expense.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert response.status_code == 303

    rows = await _expenses(vehicle_id)
    assert len(rows) == 1
    assert rows[0].deleted_at is not None
    assert await _audit("delete", str(expense.id))


async def test_expenses_respect_visibility(logged_in_client, csrf_token, anon_client, basic_user):
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="E-30", license_plate="1WB 0030", visibility="restricted",
    )
    await _add_expense(logged_in_client, csrf_token, hidden, supplier="TajnýDodavatel")
    expense = (await _expenses(hidden))[0]

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}/expenses")).status_code == 404
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}/expenses/new")).status_code == 404

    deleted = await anon_client.post(
        f"/kniha-jizd/expenses/{expense.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert deleted.status_code in (403, 404)


async def test_driver_manages_own_expense_only(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-31", license_plate="1WB 0031")
    await _add_expense(logged_in_client, csrf_token, vehicle_id)          # admin
    admin_expense = (await _expenses(vehicle_id))[0]

    await login(anon_client, basic_user)
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/expenses/new")
    assert form.status_code == 200
    driver_csrf = extract_csrf_token(form.text)
    assert (await _add_expense(anon_client, driver_csrf, vehicle_id, amount_czk="99")).status_code == 303

    # Cizí výdaj smazat nesmí
    blocked = await anon_client.post(
        f"/kniha-jizd/expenses/{admin_expense.id}/delete",
        data={"csrf_token": driver_csrf}, follow_redirects=False,
    )
    assert blocked.status_code == 403


# ======================================================================
# Doklad k servisu (PDF)
# ======================================================================

async def test_service_record_accepts_pdf_document(logged_in_client, csrf_token):
    """Doklad k servisu může být PDF - dřív šly přiložit jen obrázky."""
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleDocument, VehicleService

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="E-40", license_plate="1WC 0040")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/services/new",
        data={"csrf_token": csrf_token, "service_date": date.today().isoformat(),
              "service_type": "oprava", "description": "Oprava chlazení"},
        follow_redirects=False,
    )
    async with async_session_factory() as db:
        record = (await db.execute(
            select(VehicleService).where(VehicleService.vehicle_id == uuid.UUID(vehicle_id))
        )).scalar_one()

    response = await logged_in_client.post(
        f"/kniha-jizd/services/{record.id}/documents",
        data={"csrf_token": csrf_token},
        files={"document": ("faktura.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with async_session_factory() as db:
        docs = (await db.execute(
            select(VehicleDocument).where(VehicleDocument.service_id == record.id)
        )).scalars().all()
    assert len(docs) == 1
    assert docs[0].mime_type == "application/pdf"

    listing = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")
    assert "faktura.pdf" in listing.text
