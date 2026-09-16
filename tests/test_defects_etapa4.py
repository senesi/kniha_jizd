"""Etapa 4 - závady (zadání 16) a požadavek A - úprava časů jízdy."""
import io
import uuid
from datetime import datetime, timedelta

from PIL import Image
from sqlalchemy import select

from app.modules.reservations.calendar import LOCAL_TZ
from tests.conftest import create_vehicle, extract_csrf_token, login


def _photo_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 60, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _local(hours_ago: float) -> str:
    moment = datetime.now(LOCAL_TZ) - timedelta(hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M")


async def _defect_row(defect_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import VehicleDefect

    async with async_session_factory() as db:
        return (await db.execute(
            select(VehicleDefect).where(VehicleDefect.id == uuid.UUID(defect_id))
        )).scalar_one()


async def _trip_row(trip_id: str):
    from app.core.db import async_session_factory
    from app.models.fleet import Trip

    async with async_session_factory() as db:
        return (await db.execute(select(Trip).where(Trip.id == uuid.UUID(trip_id)))).scalar_one()


async def _report(ac, csrf, vehicle_id, **fields):
    data = {"csrf_token": csrf, "description": "Praskly stěrače", "priority": "normal", **fields}
    return await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/defects/new", data=data, follow_redirects=False)


async def _report_ok(ac, csrf, vehicle_id, **fields) -> str:
    response = await _report(ac, csrf, vehicle_id, **fields)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/defects/")[1].split("?")[0]


async def _start_trip(ac, csrf, vehicle_id, **fields) -> str:
    data = {"csrf_token": csrf, "start_odometer_km": "100000", **fields}
    response = await ac.post(f"/kniha-jizd/vehicles/{vehicle_id}/trips/start", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/trips/")[1].split("?")[0]


# ======================================================================
# Nahlášení
# ======================================================================

async def test_report_defect_from_vehicle_card(logged_in_client, csrf_token, admin_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-01", license_plate="1ZA 0001")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, priority="high")

    defect = await _defect_row(defect_id)
    assert defect.status == "new"
    assert defect.priority == "high"
    assert defect.description == "Praskly stěrače"
    assert str(defect.reported_by) == admin_user[2]
    assert defect.reported_at is not None
    assert defect.trip_id is None
    assert defect.resolved_at is None


async def test_report_defect_from_active_trip(logged_in_client, csrf_token):
    """Závada nahlášená za jízdy si pamatuje, ve které jízdě vznikla."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-02", license_plate="1ZA 0002")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    detail = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}")
    assert f"/defects/new?trip={trip_id}" in detail.text

    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, trip_id=trip_id)
    assert str((await _defect_row(defect_id)).trip_id) == trip_id


async def test_defect_photo_is_attached(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.fleet import Attachment

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-03", license_plate="1ZA 0003")
    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/defects/new",
        data={"csrf_token": csrf_token, "description": "Rozbité zrcátko", "priority": "normal"},
        files={"photo": ("zrcatko.jpg", _photo_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    defect_id = response.headers["location"].split("/defects/")[1].split("?")[0]

    async with async_session_factory() as db:
        photos = (await db.execute(
            select(Attachment).where(Attachment.defect_id == uuid.UUID(defect_id))
        )).scalars().all()
    assert len(photos) == 1
    assert photos[0].kind == "defect_photo"


async def test_description_and_priority_are_required(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-04", license_plate="1ZA 0004")

    empty = await _report(logged_in_client, csrf_token, vehicle_id, description="   ")
    assert empty.status_code == 400
    assert "Popis závady je povinný" in empty.text

    bad_priority = await _report(logged_in_client, csrf_token, vehicle_id, priority="vymyslena")
    assert bad_priority.status_code == 400


async def test_driver_can_report_but_not_resolve(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-05", license_plate="1ZA 0005")

    await login(anon_client, basic_user)
    form = await anon_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/defects/new")
    assert form.status_code == 200
    driver_csrf = extract_csrf_token(form.text)
    defect_id = await _report_ok(anon_client, driver_csrf, vehicle_id)

    # Řidič smí závadu vidět, ale ne uzavřít.
    assert (await anon_client.get(f"/kniha-jizd/defects/{defect_id}")).status_code == 200
    blocked = await anon_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": driver_csrf, "new_status": "resolved", "note": "hotovo"},
        follow_redirects=False,
    )
    assert blocked.status_code == 403
    assert (await _defect_row(defect_id)).status == "new"


# ======================================================================
# Řešení
# ======================================================================

async def test_status_flow_and_audit(logged_in_client, csrf_token, admin_user):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-10", license_plate="1ZB 0010")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id)

    progress = await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "in_progress", "note": "objednán servis"},
        follow_redirects=False,
    )
    assert progress.status_code == 303
    assert (await _defect_row(defect_id)).status == "in_progress"

    resolved = await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "resolved", "note": "vyměněny stěrače"},
        follow_redirects=False,
    )
    assert resolved.status_code == 303

    defect = await _defect_row(defect_id)
    assert defect.status == "resolved"
    assert defect.resolved_at is not None
    assert str(defect.resolved_by) == admin_user[2]
    assert defect.resolution_note == "vyměněny stěrače"

    async with async_session_factory() as db:
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.action == "status_change", AuditLog.entity_id == defect_id)
        )).scalars().all()
    assert len(entries) == 2
    assert entries[0].before_data["status"] == "new"


async def test_resolving_requires_a_note(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-11", license_plate="1ZB 0011")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "resolved", "note": "  "}, follow_redirects=False,
    )
    assert response.status_code == 400
    assert (await _defect_row(defect_id)).status == "new"


async def test_reopening_clears_resolution_stamp(logged_in_client, csrf_token):
    """Znovuotevřená závada se nesmí tvářit jako pořád vyřešená."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-12", license_plate="1ZB 0012")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id)

    await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "resolved", "note": "opraveno"}, follow_redirects=False,
    )
    await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "in_progress", "note": "znovu se objevilo"},
        follow_redirects=False,
    )

    defect = await _defect_row(defect_id)
    assert defect.status == "in_progress"
    assert defect.resolved_at is None
    assert defect.resolved_by is None


async def test_priority_change_is_audited(logged_in_client, csrf_token):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-13", license_plate="1ZB 0013")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, priority="low")

    response = await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/priority",
        data={"csrf_token": csrf_token, "priority": "critical"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert (await _defect_row(defect_id)).priority == "critical"

    async with async_session_factory() as db:
        entry = (await db.execute(
            select(AuditLog).where(AuditLog.action == "priority_change", AuditLog.entity_id == defect_id)
        )).scalar_one()
    assert entry.before_data["priority"] == "low"


# ======================================================================
# Kritická závada při výpůjčce
# ======================================================================

async def test_critical_defect_warns_before_trip(logged_in_client, csrf_token):
    """Zadání 16: kritická závada jízdu neblokuje, ale musí být výrazně
    vidět a potvrzena."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-20", license_plate="1ZC 0020")
    await _report_ok(
        logged_in_client, csrf_token, vehicle_id,
        description="Nefunkční brzdové světlo", priority="critical",
    )

    warned = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert warned.status_code == 200
    assert "KRITICKOU závadu" in warned.text
    assert "Nefunkční brzdové světlo" in warned.text
    assert 'value="critical_defect"' in warned.text

    confirmed = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000", "confirm": "critical_defect"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303


async def test_non_critical_defect_does_not_warn(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-21", license_plate="1ZC 0021")
    await _report_ok(logged_in_client, csrf_token, vehicle_id, priority="high")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert response.status_code == 303


async def test_resolved_critical_defect_stops_warning(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-22", license_plate="1ZC 0022")
    defect_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, priority="critical")
    await logged_in_client.post(
        f"/kniha-jizd/defects/{defect_id}/status",
        data={"csrf_token": csrf_token, "new_status": "resolved", "note": "opraveno v servisu"},
        follow_redirects=False,
    )

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/trips/start",
        data={"csrf_token": csrf_token, "start_odometer_km": "100000"}, follow_redirects=False,
    )
    assert response.status_code == 303

    # A v historii vozidla zůstává.
    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert card.status_code == 200
    assert (await _defect_row(defect_id)).status == "resolved"


async def test_critical_defect_shows_on_vehicle_card(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-23", license_plate="1ZC 0023")
    await _report_ok(
        logged_in_client, csrf_token, vehicle_id, description="Ujíždí řízení", priority="critical",
    )
    card = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}")
    assert "kritickou závadu" in card.text
    assert "Ujíždí řízení" in card.text


# ======================================================================
# Přehled závad
# ======================================================================

async def test_defects_list_filters(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="D-30", license_plate="1ZD 0030")
    open_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, description="Otevřená", priority="high")
    closed_id = await _report_ok(logged_in_client, csrf_token, vehicle_id, description="Uzavřená", priority="low")
    await logged_in_client.post(
        f"/kniha-jizd/defects/{closed_id}/status",
        data={"csrf_token": csrf_token, "new_status": "resolved", "note": "hotovo"}, follow_redirects=False,
    )

    open_list = await logged_in_client.get("/kniha-jizd/defects?status_filter=open")
    assert "Otevřená" in open_list.text
    assert "Uzavřená" not in open_list.text

    resolved_list = await logged_in_client.get("/kniha-jizd/defects?status_filter=resolved")
    assert "Uzavřená" in resolved_list.text

    by_priority = await logged_in_client.get("/kniha-jizd/defects?status_filter=&priority=high")
    assert "Otevřená" in by_priority.text
    assert "Uzavřená" not in by_priority.text
    assert open_id and closed_id


async def test_defects_of_hidden_vehicle_are_hidden(logged_in_client, csrf_token, anon_client, basic_user):
    """Skryté vozidlo znamená i skryté závady (požadavek D)."""
    hidden = await create_vehicle(
        logged_in_client, csrf_token, internal_code="D-31", license_plate="1ZD 0031", visibility="restricted",
    )
    defect_id = await _report_ok(logged_in_client, csrf_token, hidden, description="Tajná závada")

    await login(anon_client, basic_user)
    listing = await anon_client.get("/kniha-jizd/defects")
    assert "Tajná závada" not in listing.text
    assert (await anon_client.get(f"/kniha-jizd/defects/{defect_id}")).status_code == 404


# ======================================================================
# A) Úprava data a času jízdy
# ======================================================================

async def test_edit_trip_times(logged_in_client, csrf_token, admin_user):
    from app.core.db import async_session_factory
    from app.models.core import AuditLog

    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-01", license_plate="1CA 0001")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)
    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/end",
        data={"csrf_token": csrf_token, "end_odometer_km": "100100", "purpose_code": "montaz",
              "route_text": "tam a zpět"},
        follow_redirects=False,
    )

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/times",
        data={"csrf_token": csrf_token, "started_at": _local(5), "ended_at": _local(2),
              "reason": "řidič zapomněl jízdu ukončit"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    trip = await _trip_row(trip_id)
    assert trip.times_edited_at is not None
    assert str(trip.times_edited_by) == admin_user[2]
    assert trip.ended_at > trip.started_at

    async with async_session_factory() as db:
        entry = (await db.execute(
            select(AuditLog).where(AuditLog.action == "trip_times_edit", AuditLog.entity_id == trip_id)
        )).scalar_one()
    assert entry.before_data["started_at"]
    assert entry.after_data["reason"] == "řidič zapomněl jízdu ukončit"


async def test_edited_times_are_marked_in_the_ui(logged_in_client, csrf_token):
    """Opravený čas se nesmí tvářit jako původně naměřený."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-02", license_plate="1CA 0002")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    before = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}")
    assert "upraveno" not in before.text

    await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/times",
        data={"csrf_token": csrf_token, "started_at": _local(3), "reason": "oprava"},
        follow_redirects=False,
    )
    after = await logged_in_client.get(f"/kniha-jizd/trips/{trip_id}")
    assert "upraveno" in after.text
    assert "byly ručně upraveny" in after.text


async def test_end_before_start_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-03", license_plate="1CA 0003")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/times",
        data={"csrf_token": csrf_token, "started_at": _local(2), "ended_at": _local(5), "reason": "pokus"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "později než její začátek" in response.text
    assert (await _trip_row(trip_id)).times_edited_at is None


async def test_future_time_is_refused(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-04", license_plate="1CA 0004")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/times",
        data={"csrf_token": csrf_token, "started_at": _local(-48), "reason": "překlep v roce"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "budoucnosti" in response.text


async def test_reason_is_required(logged_in_client, csrf_token):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-05", license_plate="1CA 0005")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    response = await logged_in_client.post(
        f"/kniha-jizd/trips/{trip_id}/times",
        data={"csrf_token": csrf_token, "started_at": _local(3), "reason": "  "}, follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Zdůvodnění" in response.text


async def test_overlapping_times_warn_then_proceed(logged_in_client, csrf_token):
    """Překryv s jinou jízdou téhož vozidla je podezřelý, ne zakázaný."""
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-06", license_plate="1CA 0006")

    first = await _start_trip(logged_in_client, csrf_token, vehicle_id)
    await logged_in_client.post(
        f"/kniha-jizd/trips/{first}/end",
        data={"csrf_token": csrf_token, "end_odometer_km": "100100", "purpose_code": "montaz",
              "route_text": "první"},
        follow_redirects=False,
    )
    second = await _start_trip(logged_in_client, csrf_token, vehicle_id, start_odometer_km="100100")
    await logged_in_client.post(
        f"/kniha-jizd/trips/{second}/end",
        data={"csrf_token": csrf_token, "end_odometer_km": "100200", "purpose_code": "montaz",
              "route_text": "druhá"},
        follow_redirects=False,
    )

    # Posunout druhou jízdu doprostřed první.
    await logged_in_client.post(
        f"/kniha-jizd/trips/{first}/times",
        data={"csrf_token": csrf_token, "started_at": _local(10), "ended_at": _local(5), "reason": "oprava první"},
        follow_redirects=False,
    )
    warned = await logged_in_client.post(
        f"/kniha-jizd/trips/{second}/times",
        data={"csrf_token": csrf_token, "started_at": _local(9), "ended_at": _local(6), "reason": "oprava druhé"},
        follow_redirects=False,
    )
    assert warned.status_code == 200
    assert "překrývají s jinou jízdou" in warned.text
    assert (await _trip_row(second)).times_edited_at is None

    confirmed = await logged_in_client.post(
        f"/kniha-jizd/trips/{second}/times",
        data={"csrf_token": csrf_token, "started_at": _local(9), "ended_at": _local(6),
              "reason": "oprava druhé", "confirm": "trip_overlap"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert (await _trip_row(second)).times_edited_at is not None


async def test_stranger_cannot_edit_times(logged_in_client, csrf_token, anon_client, basic_user):
    vehicle_id = await create_vehicle(logged_in_client, csrf_token, internal_code="A-07", license_plate="1CA 0007")
    trip_id = await _start_trip(logged_in_client, csrf_token, vehicle_id)

    await login(anon_client, basic_user)
    assert (await anon_client.get(f"/kniha-jizd/trips/{trip_id}/times")).status_code == 403
