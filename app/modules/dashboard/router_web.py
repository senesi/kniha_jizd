"""Přehled (zadání 21).

Dashboard je jen souhrn - žádná vlastní business logika. Semafory počítá
app/core/fleet_status.py, tedy přesně totéž, co se zobrazuje na kartě
vozidla, aby se čísla na přehledu nikdy nerozcházela s detailem.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.core.access import visible_vehicles_condition
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes
from app.core.fleet_status import vehicle_deadlines
from app.core.templates import render_page
from app.models.core import User
from app.modules.trips import repository as trips_repository
from app.modules.vehicles import repository as vehicles_repository

router = APIRouter(tags=["dashboard-web"])


@router.get("/")
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicles = await vehicles_repository.list_vehicles(
        db, visible_to=visible_vehicles_condition(codes, user)
    )
    thresholds = await app_settings.get_all(db)

    active = [v for v in vehicles if v.is_active]
    counts = {
        "total": len(vehicles),
        "active": len(active),
        "borrowed": await trips_repository.count_active_trips(db),
        "in_service": len([v for v in active if v.status == "in_service"]),
        "blocked": len([v for v in active if v.status == "blocked"]),
    }

    # Jen vozidla, se kterými je potřeba něco udělat - zelené položky by
    # přehled jen zaplnily. Řazeno tak, aby po termínu bylo nahoře.
    attention = []
    for vehicle in active:
        actionable = [s for s in vehicle_deadlines(vehicle, thresholds) if s.is_actionable]
        if actionable:
            attention.append({
                "vehicle": vehicle,
                "statuses": actionable,
                "has_red": any(s.level == "red" for s in actionable),
            })
    attention.sort(key=lambda item: (not item["has_red"], item["vehicle"].internal_code))

    my_vehicles = await vehicles_repository.list_vehicles_for_responsible_user(db, user.id)

    return await render_page(
        request, "dashboard.html", user, db,
        counts=counts, attention=attention, my_vehicles=my_vehicles,
        my_active_trips=await trips_repository.list_active_trips_for_user(db, user.id),
        busy_vehicle_ids=await trips_repository.busy_vehicle_ids(db),
    )
