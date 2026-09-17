"""Etapa 6 - servisní historie a dokumenty vozidla (zadání 17/18/31)."""
import io
import uuid
from datetime import date, timedelta

from PIL import Image
from sqlalchemy import select

from tests.conftest import create_vehicle, extract_csrf_token, login

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (240, 240, 235)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _service_rows(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleService

    async with async_session_factory() as db:
        return list((await db.execute(
            select(VehicleService).where(VehicleService.vehicle_id == uuid.UUID(vehicle_id))
        )).scalars().all())


async def _document_rows(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleDocument

    async with async_session_factory() as db:
        return list((await db.execute(
            select(VehicleDocument).where(VehicleDocument.vehicle_id == uuid.UUID(vehicle_id))
        )).scalars().all())


async def _vehicle_row(vehicle_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Vehicle

    async with async_session_factory() as db:
        return (await db.execute(select(Vehicle).where(Vehicle.id == uuid.UUID(vehicle_id)))).scalar_one()


async def _add_service(ac, csrf, vehicle_id, **fields):
    data = {
        "csrf_token": csrf, "service_date": date.today().isoformat(),
        "service_type": "pravidelny_servis", "description": "Pravidelná prohlídka",
        **fields,
    }
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/services/new", data=data, follow_redirects=False)


async def _upload_document(ac, csrf, vehicle_id, *, content=MINIMAL_PDF, filename="tp.pdf",
                           mime="application/pdf", **fields):
    data = {"csrf_token": csrf, "doc_type": "tp", "title": "Technický průkaz", **fields}
    return await ac.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/documents",
        data=data, files={"document": (filename, content, mime)}, follow_redirects=False,
    )


# ======================================================================
# Servisní historie
# ======================================================================

async def test_add_service_record(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-01", license_plate="1SE 0001")

    response = await _add_service(
        logged_in_client, csrf_token, vehicle_id,
        odometer_km="100000", supplier="Autoservis Novák", price_czk="4 850,50", note="i filtry",
    )
    assert response.status_code == 303

    rows = await _service_rows(vehicle_id)
    assert len(rows) == 1
    record = rows[0]
    assert record.service_type == "pravidelny_servis"
    assert record.description == "Pravidelná prohlídka"
    assert record.odometer_km == 100000
    assert record.supplier == "Autoservis Novák"
    assert float(record.price_czk) == 4850.50
    assert str(record.created_by) == admin_user[2]
    assert record.deleted_at is None


async def test_service_requires_date_type_and_description(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-02", license_plate="1SE 0002")

    no_date = await _add_service(logged_in_client, csrf_token, vehicle_id, service_date="")
    assert no_date.status_code == 400
    assert "Datum servisu je povinné" in no_date.text

    no_description = await _add_service(logged_in_client, csrf_token, vehicle_id, description="   ")
    assert no_description.status_code == 400

    bad_type = await _add_service(logged_in_client, csrf_token, vehicle_id, service_type="vymyslene")
    assert bad_type.status_code == 400

    assert await _service_rows(vehicle_id) == []


async def test_future_service_date_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-03", license_plate="1SE 0003")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    response = await _add_service(logged_in_client, csrf_token, vehicle_id, service_date=tomorrow)
    assert response.status_code == 400
    assert "budoucnosti" in response.text


async def test_odometer_far_from_current_warns_then_proceeds(logged_in_client, csrf_token):
    """Zadání 32: servisní km mimo logický rozsah -> upozornění, ne
    zákaz (záznam může být dopsaný zpětně)."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="S-04", license_plate="1SE 0004",
        current_odometer_km="100000",
    )

    warned = await _add_service(logged_in_client, csrf_token, vehicle_id, odometer_km="500000")
    assert warned.status_code == 200
    assert "liší o" in warned.text
    assert 'value="odometer_range"' in warned.text
    assert await _service_rows(vehicle_id) == []

    confirmed = await _add_service(
        logged_in_client, csrf_token, vehicle_id, odometer_km="500000", confirm="odometer_range",
    )
    assert confirmed.status_code == 303
    assert (await _service_rows(vehicle_id))[0].odometer_km == 500000


async def test_oil_change_updates_vehicle_interval(logged_in_client, csrf_token):
    """Bez toho by byl olej vyměněný a semafor by dál svítil oranžově."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="S-05", license_plate="1SE 0005",
        current_odometer_km="100000", oil_interval_km="15000",
        last_oil_change_km="80000", last_oil_change_at="2025-01-01",
    )
    before = await _vehicle_row(vehicle_id)
    assert before.last_oil_change_km == 80000

    response = await _add_service(
        logged_in_client, csrf_token, vehicle_id,
        service_type="vymena_oleje", description="Výměna oleje a filtru",
        odometer_km="100000", update_oil_interval="1",
    )
    assert response.status_code == 303

    after = await _vehicle_row(vehicle_id)
    assert after.last_oil_change_km == 100000
    assert after.last_oil_change_at == date.today()


async def test_oil_interval_not_touched_without_the_checkbox(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="S-06", license_plate="1SE 0006",
        last_oil_change_km="80000",
    )
    await _add_service(
        logged_in_client, csrf_token, vehicle_id,
        service_type="vymena_oleje", description="Výměna oleje", odometer_km="100000",
    )
    assert (await _vehicle_row(vehicle_id)).last_oil_change_km == 80000


async def test_oil_interval_not_touched_by_other_service_types(logged_in_client, csrf_token):
    """Zaškrtnutí u jiného typu úkonu nesmí nic přepsat."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="S-07", license_plate="1SE 0007",
        last_oil_change_km="80000",
    )
    await _add_service(
        logged_in_client, csrf_token, vehicle_id,
        service_type="brzdy", description="Brzdové destičky", odometer_km="100000",
        update_oil_interval="1",
    )
    assert (await _vehicle_row(vehicle_id)).last_oil_change_km == 80000


async def test_invoice_photo_is_attached(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-08", license_plate="1SE 0008")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/services/new",
        data={"csrf_token": csrf_token, "service_date": date.today().isoformat(),
              "service_type": "oprava", "description": "Oprava chlazení"},
        files={"invoice": ("faktura.jpg", _image_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    record = (await _service_rows(vehicle_id))[0]
    async with async_session_factory() as db:
        attachments = (await db.execute(
            select(Attachment).where(Attachment.service_id == record.id)
        )).scalars().all()
    assert len(attachments) == 1
    assert attachments[0].kind == "service_invoice"


async def test_service_delete_is_soft(logged_in_client, csrf_token):
    """Servisní historie je podklad pro posouzení stavu vozidla - řádek
    zůstává."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-09", license_plate="1SE 0009")
    await _add_service(logged_in_client, csrf_token, vehicle_id)
    record = (await _service_rows(vehicle_id))[0]

    response = await logged_in_client.post(
        f"/kniha-jizd/services/{record.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert response.status_code == 303

    rows = await _service_rows(vehicle_id)
    assert len(rows) == 1
    assert rows[0].deleted_at is not None

    listing = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")
    assert "Pravidelná prohlídka" not in listing.text


async def test_driver_can_read_but_not_write_service_history(
    logged_in_client, csrf_token, anon_client, basic_user
):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-10", license_plate="1SF 0010")
    await _add_service(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, basic_user)
    listing = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")
    assert listing.status_code == 200
    assert "Pravidelná prohlídka" in listing.text
    assert "Přidat úkon" not in listing.text

    assert (await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services/new")).status_code == 403


async def test_responsible_person_manages_own_vehicle_service(
    logged_in_client, csrf_token, anon_client, responsible_user
):
    _, _, responsible_id = responsible_user
    mine = await create_vehicle(
        logged_in_client, csrf_token, internal_code="S-11", license_plate="1SF 0011",
        responsible_user_id=responsible_id,
    )
    other = await create_vehicle(logged_in_client, csrf_token, internal_code="S-12", license_plate="1SF 0012")

    await login(anon_client, responsible_user)
    form = await anon_client.get(f"/kniha-jizd/vehicles/{mine}/services/new")
    assert form.status_code == 200
    assert (await _add_service(anon_client, extract_csrf_token(form.text), mine)).status_code == 303

    # Cizí vozidlo ne
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{other}/services/new")).status_code == 403


async def test_service_totals(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="S-13", license_plate="1SF 0013")
    await _add_service(logged_in_client, csrf_token, vehicle_id, price_czk="1000")
    await _add_service(logged_in_client, csrf_token, vehicle_id, price_czk="2500,50", service_type="brzdy")

    listing = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/services")
    assert "3 500,50" in listing.text   # nezlomitelná mezera v tisících

    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "2 úkony" in card.text


# ======================================================================
# Dokumenty vozidla
# ======================================================================

async def test_upload_and_download_document(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-01", license_plate="1DO 0001")

    response = await _upload_document(
        logged_in_client, csrf_token, vehicle_id,
        valid_from="2026-01-01", valid_to="2027-01-01", note="originál v trezoru",
    )
    assert response.status_code == 303

    rows = await _document_rows(vehicle_id)
    assert len(rows) == 1
    document = rows[0]
    assert document.doc_type == "tp"
    assert document.title == "Technický průkaz"
    assert document.mime_type == "application/pdf"
    assert document.size_bytes == len(MINIMAL_PDF)
    assert str(document.uploaded_by) == admin_user[2]
    # Jméno na disku je náhodné UUID, ne to původní (zadání 18/30)
    assert document.stored_filename != document.original_filename
    assert document.original_filename == "tp.pdf"

    download = await logged_in_client.get(f"/kniha-jizd/documents/{document.id}/file")
    assert download.status_code == 200
    assert download.content == MINIMAL_PDF
    assert download.headers["content-type"] == "application/pdf"
    assert "private" in download.headers["cache-control"]


async def test_document_content_must_match_extension(logged_in_client, csrf_token):
    """Soubor, který tvrdí .pdf, ale nezačíná %PDF-, se odmítne."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-02", license_plate="1DO 0002")

    response = await _upload_document(
        logged_in_client, csrf_token, vehicle_id, content=b"tohle neni pdf", filename="podvrh.pdf",
    )
    assert response.status_code == 400
    assert await _document_rows(vehicle_id) == []


async def test_unsupported_document_type_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-03", license_plate="1DO 0003")
    response = await _upload_document(
        logged_in_client, csrf_token, vehicle_id,
        content=b"MZ\x90\x00", filename="virus.exe", mime="application/octet-stream",
    )
    assert response.status_code == 400


async def test_document_validity_range_is_checked(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-04", license_plate="1DO 0004")
    response = await _upload_document(
        logged_in_client, csrf_token, vehicle_id, valid_from="2027-01-01", valid_to="2026-01-01",
    )
    assert response.status_code == 400


async def test_driver_can_read_documents_but_not_upload(logged_in_client, csrf_token, anon_client, basic_user):
    """Zelená karta je přesně to, co řidič potřebuje při kontrole -
    čtení tedy smí každý, kdo vidí vozidlo (ROZHODNUTI.md R24)."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-10", license_plate="1DP 0010")
    await _upload_document(logged_in_client, csrf_token, vehicle_id, title="Zelená karta")
    document = (await _document_rows(vehicle_id))[0]

    await login(anon_client, basic_user)
    listing = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/documents")
    assert listing.status_code == 200
    assert "Zelená karta" in listing.text
    assert "Nahrát dokument" not in listing.text

    # Stáhnout smí
    download = await anon_client.get(f"/kniha-jizd/documents/{document.id}/file")
    assert download.status_code == 200

    # Nahrát ani smazat ne
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start")
    driver_csrf = extract_csrf_token(form.text)
    assert (await _upload_document(anon_client, driver_csrf, vehicle_id)).status_code == 403
    deleted = await anon_client.post(
        f"/kniha-jizd/documents/{document.id}/delete",
        data={"csrf_token": driver_csrf}, follow_redirects=False,
    )
    assert deleted.status_code == 403
    assert (await _document_rows(vehicle_id))[0].deleted_at is None


async def test_documents_of_hidden_vehicle_are_hidden(logged_in_client, csrf_token, anon_client, basic_user):
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="D2-11", license_plate="1DP 0011", visibility="restricted",
    )
    await _upload_document(logged_in_client, csrf_token, hidden, title="Tajný dokument")
    document = (await _document_rows(hidden))[0]

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/vehicles/{hidden}/documents")).status_code == 404
    assert (await anon_client.get(f"/kniha-jizd/documents/{document.id}/file")).status_code == 404


async def test_deleted_document_is_immediately_unreachable(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-12", license_plate="1DP 0012")
    await _upload_document(logged_in_client, csrf_token, vehicle_id)
    document = (await _document_rows(vehicle_id))[0]

    assert (await logged_in_client.get(f"/kniha-jizd/documents/{document.id}/file")).status_code == 200

    response = await logged_in_client.post(
        f"/kniha-jizd/documents/{document.id}/delete",
        data={"csrf_token": csrf_token}, follow_redirects=False,
    )
    assert response.status_code == 303

    # Soft delete, ale hned nedostupné
    assert (await _document_rows(vehicle_id))[0].deleted_at is not None
    assert (await logged_in_client.get(f"/kniha-jizd/documents/{document.id}/file")).status_code == 404


async def test_expiring_document_is_flagged(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-13", license_plate="1DP 0013")

    await _upload_document(
        logged_in_client, csrf_token, vehicle_id, title="Propadlá pojistka",
        valid_to=(date.today() - timedelta(days=5)).isoformat(),
    )
    await _upload_document(
        logged_in_client, csrf_token, vehicle_id, title="Končící známka",
        valid_to=(date.today() + timedelta(days=10)).isoformat(),
    )

    listing = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/documents")
    assert "Platnost vypršela" in listing.text
    assert "Platnost končí" in listing.text


async def test_image_document_is_accepted(logged_in_client, csrf_token):
    """Fotka TP z telefonu je legitimní dokument."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D2-14", license_plate="1DP 0014")
    response = await _upload_document(
        logged_in_client, csrf_token, vehicle_id,
        content=_image_bytes(), filename="tp.jpg", mime="image/jpeg",
    )
    assert response.status_code == 303
    assert (await _document_rows(vehicle_id))[0].mime_type == "image/jpeg"
