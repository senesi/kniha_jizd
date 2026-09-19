"""Etapa 5 - tankování a nabíjení, účtenky, OCR (zadání 15/25/31)."""
import io
import uuid
from datetime import date, timedelta

from PIL import Image
from sqlalchemy import select

from app.core.ocr import parse_receipt_text
from tests.conftest import create_vehicle, extract_csrf_token, login


def _receipt_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (700, 1000), (245, 245, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _fueling_rows(trip_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import TripFueling

    async with async_session_factory() as db:
        return list((await db.execute(
            select(TripFueling).where(TripFueling.trip_id == uuid.UUID(trip_id))
        )).scalars().all())


async def _start_trip(ac, csrf, vehicle_id, **fields) -> str:
    data = {"csrf_token": csrf, "start_odometer_km": "100000", **fields}
    response = await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/trips/")[1].split("?")[0]


async def _add(ac, csrf, trip_id, **fields):
    data = {
        "csrf_token": csrf, "fueled_at": date.today().isoformat(),
        "quantity": "48,5", "unit": "l", **fields,
    }
    return await ac.post(f"/kniha-jizd/trips/{trip_id}/fuelings/new", data=data, follow_redirects=False)


# ======================================================================
# Čtení účtenky (čisté funkce, bez OCR enginu)
# ======================================================================

def test_receipt_parsing_czech_diesel():
    reading = parse_receipt_text(
        "CCS Benzina Mlada Boleslav\n12.3.2026 14:22\nNafta 48,50 l\n38,90 Kc/l\nCelkem 1 887,15 Kc"
    )
    assert reading.fueled_at == date(2026, 3, 12)
    assert reading.quantity == 48.5
    assert reading.unit == "l"
    assert reading.price_per_unit_czk == 38.90
    # Tisíce oddělené mezerou musí projít.
    assert reading.price_total_czk == 1887.15


def test_receipt_parsing_kwh():
    """kWh se nesmí splést s litry - obsahuje 'h', ne 'l'."""
    reading = parse_receipt_text("ChargeUp\n2026-03-12\n37,2 kWh\nCelkem 312,48 Kc")
    assert reading.unit == "kWh"
    assert reading.quantity == 37.2
    assert reading.fueled_at == date(2026, 3, 12)


def test_receipt_parsing_ignores_random_numbers():
    """Číslo karty ani IČO se nesmí splést s cenou."""
    reading = parse_receipt_text("Karta 4556123412341234\nICO 12345678\nNafta 10 l")
    assert reading.quantity == 10
    assert reading.price_total_czk is None


def test_receipt_parsing_returns_none_when_nothing_found():
    assert parse_receipt_text("dekujeme za nakup") is None
    assert parse_receipt_text("") is None


def test_receipt_parsing_survives_broken_date():
    reading = parse_receipt_text("32.13.2026\nNafta 20 l")
    assert reading.quantity == 20
    assert reading.fueled_at is None


# ======================================================================
# Zadání tankování
# ======================================================================

async def test_add_fueling_to_trip(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-01", license_plate="1TA 0001")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await _add(
        logged_in_client, csrf_token, trip_id,
        price_total_czk="1887,15", station="Benzina Mladá Boleslav", odometer_km="100050",
    )
    assert response.status_code == 303

    rows = await _fueling_rows(trip_id)
    assert len(rows) == 1
    fueling = rows[0]
    assert float(fueling.quantity) == 48.5
    assert fueling.unit == "l"
    assert float(fueling.price_total_czk) == 1887.15
    assert fueling.station == "Benzina Mladá Boleslav"
    assert fueling.odometer_km == 100050
    assert str(fueling.created_by) == admin_user[2]
    # vehicle_id se denormalizuje z jízdy
    assert str(fueling.vehicle_id) == vehicle_id


async def test_price_per_unit_is_derived_when_missing(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-02", license_plate="1TA 0002")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    await _add(logged_in_client, csrf_token, trip_id, quantity="50", price_total_czk="1950")
    fueling = (await _fueling_rows(trip_id))[0]
    assert float(fueling.price_per_unit_czk) == 39.0


async def test_entered_price_per_unit_is_never_overwritten(logged_in_client, csrf_token):
    """Na účtence může být zaokrouhleno jinak, než by vyšlo z dělení."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-03", license_plate="1TA 0003")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    await _add(
        logged_in_client, csrf_token, trip_id,
        quantity="50", price_total_czk="1950", price_per_unit_czk="38,90",
    )
    fueling = (await _fueling_rows(trip_id))[0]
    assert float(fueling.price_per_unit_czk) == 38.90


async def test_date_and_quantity_are_required(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-04", license_plate="1TA 0004")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    no_date = await _add(logged_in_client, csrf_token, trip_id, fueled_at="")
    assert no_date.status_code == 400
    assert "Datum je povinné" in no_date.text

    no_quantity = await _add(logged_in_client, csrf_token, trip_id, quantity="")
    assert no_quantity.status_code == 400

    zero = await _add(logged_in_client, csrf_token, trip_id, quantity="0")
    assert zero.status_code == 400

    negative = await _add(logged_in_client, csrf_token, trip_id, quantity="-5")
    assert negative.status_code == 400

    assert await _fueling_rows(trip_id) == []


async def test_future_date_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-05", license_plate="1TA 0005")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    response = await _add(logged_in_client, csrf_token, trip_id, fueled_at=tomorrow)
    assert response.status_code == 400
    assert "budoucnosti" in response.text


async def test_absurd_quantity_is_refused_hard(logged_in_client, csrf_token):
    """Výchozí strop je 300 l - nad ním je to zjevně chyba, ne kanystr."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-06", license_plate="1TA 0006")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await _add(logged_in_client, csrf_token, trip_id, quantity="5000")
    assert response.status_code == 400
    assert "maximum" in response.text
    assert await _fueling_rows(trip_id) == []


async def test_over_capacity_warns_then_proceeds(logged_in_client, csrf_token):
    """Víc, než se vejde do nádrže, je podezřelé - ale kanystr je
    legitimní, takže jen varování."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="F-07", license_plate="1TA 0007", tank_capacity_l="50",
    )
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    warned = await _add(logged_in_client, csrf_token, trip_id, quantity="80")
    assert warned.status_code == 200
    assert "kapacita vozidla" in warned.text
    assert 'value="over_capacity"' in warned.text
    assert await _fueling_rows(trip_id) == []

    confirmed = await _add(logged_in_client, csrf_token, trip_id, quantity="80", confirm="over_capacity")
    assert confirmed.status_code == 303
    assert float((await _fueling_rows(trip_id))[0].quantity) == 80


async def test_odometer_before_trip_start_warns(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-08", license_plate="1TA 0008")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100000")

    warned = await _add(logged_in_client, csrf_token, trip_id, odometer_km="90000")
    assert warned.status_code == 200
    assert "nižší než na začátku jízdy" in warned.text

    confirmed = await _add(
        logged_in_client, csrf_token, trip_id, odometer_km="90000", confirm="odometer_before_trip",
    )
    assert confirmed.status_code == 303


# ======================================================================
# Elektromobil vs. spalovací
# ======================================================================

async def test_electric_vehicle_uses_kwh(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="F-10", license_plate="1TB 0010",
        fuel_type="elektro", battery_capacity_kwh="77",
    )
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    form = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}/fuelings/new")
    assert form.status_code == 200
    assert "Nabíjení" in form.text
    assert "Nabito (kWh)" in form.text
    assert "Nabíjecí stanice" in form.text
    # Litry se u elektromobilu nesmí vůbec nabídnout
    assert "Natankováno (l)" not in form.text

    response = await _add(logged_in_client, csrf_token, trip_id, quantity="37,2", unit="kWh")
    assert response.status_code == 303
    fueling = (await _fueling_rows(trip_id))[0]
    assert fueling.unit == "kWh"
    assert float(fueling.quantity) == 37.2


async def test_litres_are_refused_for_electric_vehicle(logged_in_client, csrf_token):
    """Podvržená jednotka ve formuláři nesmí projít."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="F-11", license_plate="1TB 0011", fuel_type="elektro",
    )
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await _add(logged_in_client, csrf_token, trip_id, unit="l")
    assert response.status_code == 400
    assert "kWh" in response.text
    assert await _fueling_rows(trip_id) == []


async def test_hybrid_offers_both_units(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="F-12", license_plate="1TB 0012", fuel_type="hybrid",
    )
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    assert (await _add(logged_in_client, csrf_token, trip_id, quantity="30", unit="l")).status_code == 303
    assert (await _add(logged_in_client, csrf_token, trip_id, quantity="12", unit="kWh")).status_code == 303

    units = {row.unit for row in await _fueling_rows(trip_id)}
    assert units == {"l", "kWh"}


async def test_kwh_limit_is_separate_from_litres(logged_in_client, csrf_token):
    """Strop pro kWh je vlastní (250), ne stejný jako pro litry (300)."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="F-13", license_plate="1TB 0013", fuel_type="elektro",
    )
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await _add(logged_in_client, csrf_token, trip_id, quantity="280", unit="kWh")
    assert response.status_code == 400
    assert "250 kWh" in response.text


# ======================================================================
# Účtenka a OCR
# ======================================================================

async def test_receipt_photo_is_attached(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-20", license_plate="1TC 0020")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": date.today().isoformat(), "quantity": "40", "unit": "l"},
        files={"receipt": ("uctenka.jpg", _receipt_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    fueling = (await _fueling_rows(trip_id))[0]
    async with async_session_factory() as db:
        receipts = (await db.execute(
            select(Attachment).where(Attachment.fueling_id == fueling.id)
        )).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].kind == "fuel_receipt"
    assert str(receipts[0].trip_id) == trip_id


async def test_ocr_result_is_offered_for_confirmation(logged_in_client, csrf_token, monkeypatch):
    """Zadání 25: OCR výsledek se vždy zobrazí uživateli a teprve po
    potvrzení se uloží."""
    from app.core import ocr

    async def fake_read(image_bytes, known_stations=()):
        return ocr.ReceiptReading(
            fueled_at=date(2026, 3, 12), quantity=48.5, unit="l",
            price_per_unit_czk=38.90, price_total_czk=1887.15, raw_text="x",
        )

    monkeypatch.setattr(ocr, "read_receipt", fake_read)

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-21", license_plate="1TC 0021")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    asked = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": date.today().isoformat(), "quantity": "1", "unit": "l"},
        files={"receipt": ("uctenka.jpg", _receipt_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert asked.status_code == 200
    assert "Z účtenky jsme přečetli" in asked.text
    # Filtr `money` oddeluje tisice nezlomitelnou mezerou (U+00A0).
    assert "1 887,15" in asked.text
    assert 'name="receipt_attachment_id"' in asked.text

    # Zatím se nic neuložilo
    assert await _fueling_rows(trip_id) == []

    attachment_id = asked.text.split('name="receipt_attachment_id" value="')[1].split('"')[0]

    confirmed = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": "2026-03-12", "quantity": "48,5", "unit": "l",
              "price_total_czk": "1887,15", "confirm": "ocr", "receipt_attachment_id": attachment_id},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303

    fueling = (await _fueling_rows(trip_id))[0]
    assert float(fueling.quantity) == 48.5
    assert fueling.ocr_confirmed is True

    # Účtenka z prvního kroku se napojila, a jen jednou
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment
    async with async_session_factory() as db:
        receipts = (await db.execute(
            select(Attachment).where(Attachment.fueling_id == fueling.id)
        )).scalars().all()
    assert len(receipts) == 1
    assert str(receipts[0].id) == attachment_id


async def test_ocr_failure_never_blocks_fueling(logged_in_client, csrf_token, monkeypatch):
    from app.core import ocr

    async def exploding(image_bytes):
        raise RuntimeError("OCR spadlo")

    monkeypatch.setattr(ocr, "_extract_text", exploding)
    monkeypatch.setattr(ocr, "is_configured", lambda: True)

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-22", license_plate="1TC 0022")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": date.today().isoformat(), "quantity": "40", "unit": "l"},
        files={"receipt": ("uctenka.jpg", _receipt_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert len(await _fueling_rows(trip_id)) == 1


async def test_non_image_receipt_is_rejected(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-23", license_plate="1TC 0023")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/fuelings/new",
        data={"csrf_token": csrf_token, "fueled_at": date.today().isoformat(), "quantity": "40", "unit": "l"},
        files={"receipt": ("uctenka.pdf", b"%PDF-1.4 nope", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 400


# ======================================================================
# Oprávnění a mazání
# ======================================================================

async def test_stranger_cannot_add_fueling(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-30", license_plate="1TD 0030")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/trips/{trip_id}/fuelings/new")).status_code == 403


async def test_driver_can_add_and_delete_own_fueling(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-31", license_plate="1TD 0031")

    await login(anon_client, basic_user)
    # Karta vozidla pro běžného řidiče žádný formulář (a tedy CSRF token)
    # nemá - token se bere z formuláře zahájení jízdy.
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    driver_csrf = extract_csrf_token(form.text)
    trip_id = await _start_trip(anon_client, driver_csrf, vehicle_id)

    assert (await _add(anon_client, driver_csrf, trip_id)).status_code == 303
    fueling = (await _fueling_rows(trip_id))[0]

    deleted = await anon_client.post(
        f"/kniha-jizd/fuelings/{fueling.id}/delete",
        data={"csrf_token": driver_csrf}, follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert await _fueling_rows(trip_id) == []


async def test_fueling_can_be_added_after_trip_ends(logged_in_client, csrf_token):
    """Účtenku řidič často doplní až po návratu."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-32", license_plate="1TD 0032")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/end",
        data={"csrf_token": csrf_token, "end_odometer_km": "100100", "purpose_code": "montaz",
              "route_text": "tam a zpět"},
        follow_redirects=False,
    )

    assert (await _add(logged_in_client, csrf_token, trip_id)).status_code == 303
    assert len(await _fueling_rows(trip_id)) == 1


async def test_fueling_shows_on_trip_and_vehicle(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="F-33", license_plate="1TD 0033")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)
    await _add(logged_in_client, csrf_token, trip_id, quantity="48,5", price_total_czk="1887,15")

    trip_page = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}")
    assert "48,5 l" in trip_page.text
    assert "1 887,15" in trip_page.text

    vehicle_page = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "48,5 l" in vehicle_page.text
