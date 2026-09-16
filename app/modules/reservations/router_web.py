"""Rezervace a kalendář (zadání 9).

Hlavní stránkou je kalendář, ne seznam - řidič potřebuje vidět, co je
volné, ne co je obsazené.
"""
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_permission
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import Vehicle, VehicleReservation
from app.modules.reservations import calendar, repository, service
from app.modules.vehicles import repository as vehicles_repository

reservations_router = APIRouter(tags=["reservations-web"])
vehicle_reservations_router = APIRouter(tags=["reservations-web"])

CREATE = "fleet.reservation.create"
MANAGE = "fleet.reservation.manage"


# --- pomocné ----------------------------------------------------------

async def _load(db: AsyncSession, reservation_id: uuid.UUID) -> VehicleReservation:
    reservation = await repository.get(db, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rezervace nebyla nalezena.")
    return reservation


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    return vehicle


def _parse_local(raw: str | None) -> datetime | None:
    """<input type="datetime-local"> posílá „2026-09-20T08:00" bez zóny.
    Doplní se provozní zóna, aby se do databáze uložil správný okamžik -
    ne čas posunutý o UTC offset."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        return None
    return naive.replace(tzinfo=calendar.LOCAL_TZ) if naive.tzinfo is None else naive


def _to_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def _form_value(form, name: str) -> str | None:
    raw = form.get(name)
    return raw if isinstance(raw, str) else None


# --- kalendář ---------------------------------------------------------

@reservations_router.get("/reservations")
async def reservations_calendar(
    request: Request,
    week: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        anchor = date.fromisoformat(week) if week else datetime.now(calendar.LOCAL_TZ).date()
    except ValueError:
        anchor = datetime.now(calendar.LOCAL_TZ).date()

    start = calendar.week_start(anchor)
    days = calendar.week_days(start)
    window_start, window_end = calendar.window_bounds(days)

    vehicles = await vehicles_repository.list_vehicles(db, active_only=True)
    reservations = await repository.list_in_window(db, start=window_start, end=window_end)
    active_trips = await repository.active_trips_in_window(db, start=window_start, end=window_end)

    codes = await get_user_permission_codes(db, user.id)
    return await render_page(
        request, "reservations_calendar.html", user, db,
        rows=calendar.build_rows(vehicles, reservations, active_trips, days),
        days=days, day_names=calendar.DAY_NAMES,
        week_start=start,
        prev_week=(start - timedelta(days=7)).isoformat(),
        next_week=(start + timedelta(days=7)).isoformat(),
        this_week=datetime.now(calendar.LOCAL_TZ).date().isoformat(),
        reservations=reservations,
        can_create=CREATE in codes,
        can_manage=MANAGE in codes,
    )


@reservations_router.get("/reservations/mine")
async def my_reservations(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "my_reservations.html", user, db,
        reservations=await repository.list_for_user(db, user.id, include_past=True),
    )


# --- vytvoření --------------------------------------------------------
# Pozor na pořadí: /reservations/new i /reservations/mine musí být
# registrované PŘED /reservations/{reservation_id}.

@reservations_router.get("/reservations/new")
async def reservation_new_form(
    request: Request,
    vehicle: str = "",
    day: str = "",
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    prefill = {}
    if day:
        # Proklik z kalendáře: předvyplní se rozumný pracovní den, ať
        # uživatel nevyťukává datum ručně.
        prefill = {"start_at": f"{day}T08:00", "end_at": f"{day}T16:00"}
    if vehicle:
        prefill["vehicle_id"] = vehicle

    return await render_page(
        request, "reservation_form.html", user, db,
        reservation=None, vehicles=await vehicles_repository.list_vehicles(db, active_only=True),
        error=None, form=prefill, can_create_service=MANAGE in codes,
    )


@reservations_router.post("/reservations/new", dependencies=[Depends(verify_csrf)])
async def reservation_create(
    request: Request,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    codes = await get_user_permission_codes(db, user.id)
    payload = {
        "vehicle_id": _form_value(form, "vehicle_id"),
        "start_at": _form_value(form, "start_at"),
        "end_at": _form_value(form, "end_at"),
        "purpose": _form_value(form, "purpose"),
        "note": _form_value(form, "note"),
        "expected_route": _form_value(form, "expected_route"),
        "expected_km": _form_value(form, "expected_km"),
        "kind": _form_value(form, "kind") or "reservation",
    }

    async def rerender(error: str):
        return await render_page(
            request, "reservation_form.html", user, db, status_code=400,
            reservation=None, vehicles=await vehicles_repository.list_vehicles(db, active_only=True),
            error=error, form=payload, can_create_service=MANAGE in codes,
        )

    if not payload["vehicle_id"]:
        return await rerender("Vyberte vozidlo.")
    # Servisní blok smí založit jen správce - jinak by si kdokoliv mohl
    # odstavit vozidlo bez majitele rezervace.
    if payload["kind"] == "service" and MANAGE not in codes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění odstavit vozidlo.")

    try:
        vehicle = await _load_vehicle(db, uuid.UUID(payload["vehicle_id"]))
    except ValueError:
        return await rerender("Vyberte vozidlo.")

    try:
        reservation = await service.create_reservation(
            db, vehicle=vehicle, actor=user,
            start_at=_parse_local(payload["start_at"]), end_at=_parse_local(payload["end_at"]),
            purpose=payload["purpose"], note=payload["note"],
            expected_route=payload["expected_route"], expected_km=_to_int(payload["expected_km"]),
            kind=payload["kind"],
        )
    except service.ReservationError as error:
        return await rerender(str(error))

    return flash.redirect(f"/kniha-jizd/reservations/{reservation.id}", "reservation_created")


@vehicle_reservations_router.get("/vehicles/{vehicle_id}/reservations")
async def vehicle_reservations(
    vehicle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Z karty vozidla rovnou do kalendáře s předvybraným vozidlem."""
    await _load_vehicle(db, vehicle_id)
    return flash.redirect(f"/kniha-jizd/reservations/new?vehicle={vehicle_id}")


# --- detail, úprava, zrušení ------------------------------------------

@reservations_router.get("/reservations/{reservation_id}")
async def reservation_detail(
    request: Request,
    reservation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reservation = await _load(db, reservation_id)
    codes = await get_user_permission_codes(db, user.id)
    return await render_page(
        request, "reservation_detail.html", user, db,
        reservation=reservation,
        can_manage=service.can_manage_reservation(reservation, user, codes),
    )


@reservations_router.get("/reservations/{reservation_id}/edit")
async def reservation_edit_form(
    request: Request,
    reservation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reservation = await _load(db, reservation_id)
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_manage_reservation(reservation, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tato rezervace není vaše.")
    return await render_page(
        request, "reservation_form.html", user, db,
        reservation=reservation, vehicles=await vehicles_repository.list_vehicles(db, active_only=True),
        error=None, form={}, can_create_service=MANAGE in codes,
    )


@reservations_router.post("/reservations/{reservation_id}/edit", dependencies=[Depends(verify_csrf)])
async def reservation_update(
    request: Request,
    reservation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reservation = await _load(db, reservation_id)
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_manage_reservation(reservation, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tato rezervace není vaše.")

    form = await request.form()
    payload = {
        "start_at": _form_value(form, "start_at"),
        "end_at": _form_value(form, "end_at"),
        "purpose": _form_value(form, "purpose"),
        "note": _form_value(form, "note"),
        "expected_route": _form_value(form, "expected_route"),
        "expected_km": _form_value(form, "expected_km"),
    }
    try:
        await service.update_reservation(
            db, reservation=reservation, actor=user,
            start_at=_parse_local(payload["start_at"]), end_at=_parse_local(payload["end_at"]),
            purpose=payload["purpose"], note=payload["note"],
            expected_route=payload["expected_route"], expected_km=_to_int(payload["expected_km"]),
        )
    except service.ReservationError as error:
        return await render_page(
            request, "reservation_form.html", user, db, status_code=400,
            reservation=reservation, vehicles=await vehicles_repository.list_vehicles(db, active_only=True),
            error=str(error), form=payload, can_create_service=MANAGE in codes,
        )
    return flash.redirect(f"/kniha-jizd/reservations/{reservation.id}", "reservation_updated")


@reservations_router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(verify_csrf)])
async def reservation_cancel(
    reservation_id: uuid.UUID,
    reason: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reservation = await _load(db, reservation_id)
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_manage_reservation(reservation, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tato rezervace není vaše.")
    try:
        await service.cancel_reservation(db, reservation=reservation, actor=user, reason=reason)
    except service.ReservationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/reservations/{reservation.id}", "reservation_cancelled")
