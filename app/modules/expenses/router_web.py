"""Výdaje na vozidlo.

Mobilní tok je krátký schválně: nový výdaj → vyfotit doklad → částka →
uložit. Datum se předvyplní dneškem, druh výdaje je dlaždicový výběr a
zbytek se schová pod rozbalovátko.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.access import (
    MANAGE_ANY,
    MANAGE_OWN,
    assert_vehicle_visible,
    can_manage_vehicle,
)
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_permission
from app.core.documents import DocumentTooLarge, UnsupportedDocumentType
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import EXPENSE_TYPES, Vehicle
from app.modules.expenses import repository, service
from app.modules.trips import repository as trips_repository
from app.modules.vehicles import repository as vehicles_repository

expenses_router = APIRouter(tags=["expenses-web"])
vehicle_expenses_router = APIRouter(tags=["expenses-web"])

CREATE = "fleet.trip.create"


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User, codes: set[str]) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)
    return vehicle


def _to_float(raw) -> float | None:
    text = str(raw or "").strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def _to_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(form, name: str) -> str | None:
    raw = form.get(name)
    return raw if isinstance(raw, str) else None


async def _read_receipt(upload: UploadFile | None):
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    return (upload.filename, upload.content_type, data) if data else None


# --- přehled výdajů -----------------------------------------------------

@vehicle_expenses_router.get("/vehicles/{vehicle_id}/expenses")
async def vehicle_expenses(
    request: Request,
    vehicle_id: uuid.UUID,
    date_from: str = "",
    date_to: str = "",
    expense_type: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)

    parsed_from = _to_date(date_from)
    parsed_to = _to_date(date_to)

    return await render_page(
        request, "expenses_list.html", user, db,
        vehicle=vehicle,
        expenses=await repository.list_for_vehicle(
            db, vehicle.id, date_from=parsed_from, date_to=parsed_to, expense_type=expense_type,
        ),
        totals=await repository.combined_totals(db, vehicle.id, date_from=parsed_from, date_to=parsed_to),
        expense_types=EXPENSE_TYPES,
        date_from=date_from, date_to=date_to, expense_type=expense_type,
        # Rychlé volby období - nejčastější je "letos" a "tento měsíc".
        quick_ranges=_quick_ranges(),
        can_manage=can_manage_vehicle(codes, vehicle, user),
    )


def _quick_ranges() -> list[dict]:
    today = date.today()
    return [
        {"label": "Tento měsíc", "date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()},
        {"label": "Letos", "date_from": today.replace(month=1, day=1).isoformat(), "date_to": today.isoformat()},
        {"label": "Posledních 12 měsíců",
         "date_from": (today - timedelta(days=365)).isoformat(), "date_to": today.isoformat()},
        {"label": "Vše", "date_from": "", "date_to": ""},
    ]


# --- nový výdaj ---------------------------------------------------------

@vehicle_expenses_router.get("/vehicles/{vehicle_id}/expenses/new")
async def expense_new_form(
    request: Request,
    vehicle_id: uuid.UUID,
    trip: str = "",
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)

    return await render_page(
        request, "expense_form.html", user, db,
        vehicle=vehicle, expense_types=EXPENSE_TYPES,
        trips=await repository.list_trips_for_picker(db, vehicle.id),
        error=None, warnings=[],
        form={
            "expense_date": date.today().isoformat(),
            "odometer_km": str(vehicle.current_odometer_km),
            "trip_id": trip,
        },
    )


@vehicle_expenses_router.post("/vehicles/{vehicle_id}/expenses/new", dependencies=[Depends(verify_csrf)])
async def expense_create(
    request: Request,
    vehicle_id: uuid.UUID,
    receipt: UploadFile | None = File(None),
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)

    form = await request.form()
    payload = {
        "expense_date": _text(form, "expense_date"),
        "expense_type": _text(form, "expense_type") or "",
        "amount_czk": _text(form, "amount_czk"),
        "amount_net_czk": _text(form, "amount_net_czk"),
        "vat_czk": _text(form, "vat_czk"),
        "currency": _text(form, "currency") or "CZK",
        "supplier": _text(form, "supplier"),
        "odometer_km": _text(form, "odometer_km"),
        "note": _text(form, "note"),
        "trip_id": _text(form, "trip_id") or "",
    }
    confirmations = {value for value in form.getlist("confirm") if isinstance(value, str)}

    async def rerender(error, warnings, status_code=400):
        return await render_page(
            request, "expense_form.html", user, db, status_code=status_code,
            vehicle=vehicle, expense_types=EXPENSE_TYPES,
            trips=await repository.list_trips_for_picker(db, vehicle.id),
            error=error, warnings=warnings, form=payload,
        )

    trip = None
    if payload["trip_id"]:
        try:
            trip = await trips_repository.get_trip(db, uuid.UUID(payload["trip_id"]))
        except ValueError:
            trip = None
        if trip is None:
            return await rerender("Vybraná jízda nebyla nalezena.", [])

    try:
        expense = await service.add_expense(
            db, vehicle=vehicle, actor=user,
            expense_date=_to_date(payload["expense_date"]),
            expense_type=payload["expense_type"],
            amount_czk=_to_float(payload["amount_czk"]),
            amount_net_czk=_to_float(payload["amount_net_czk"]),
            vat_czk=_to_float(payload["vat_czk"]),
            currency=payload["currency"],
            supplier=payload["supplier"],
            odometer_km=_to_int(payload["odometer_km"]),
            note=payload["note"],
            trip=trip,
            confirmations=confirmations,
            receipt=await _read_receipt(receipt),
        )
    except service.ExpenseWarning as warning:
        return await rerender(None, [warning], status_code=200)
    except (service.ExpenseError, DocumentTooLarge, UnsupportedDocumentType) as error:
        return await rerender(str(error), [])

    assert expense is not None
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/expenses", "expense_added")


# --- doklad a mazání ----------------------------------------------------

@expenses_router.post("/expenses/{expense_id}/receipt", dependencies=[Depends(verify_csrf)])
async def expense_add_receipt(
    expense_id: uuid.UUID,
    receipt: UploadFile = File(...),
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    expense = await repository.get(db, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Výdaj nebyl nalezen.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, expense.vehicle, user)
    _assert_can_edit(expense, user, codes)

    data = await _read_receipt(receipt)
    if data is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vyberte soubor.")
    try:
        await service.add_receipt(db, expense=expense, actor=user, receipt=data)
    except (DocumentTooLarge, UnsupportedDocumentType) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/vehicles/{expense.vehicle_id}/expenses", "receipt_added")


@expenses_router.post("/expenses/{expense_id}/delete", dependencies=[Depends(verify_csrf)])
async def expense_delete(
    expense_id: uuid.UUID,
    user: User = Depends(require_permission(CREATE)),
    db: AsyncSession = Depends(get_db),
):
    expense = await repository.get(db, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Výdaj nebyl nalezen.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, expense.vehicle, user)
    _assert_can_edit(expense, user, codes)

    vehicle_id = expense.vehicle_id
    await service.delete_expense(db, expense=expense, actor=user)
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle_id}/expenses", "expense_deleted")


def _assert_can_edit(expense, actor: User, codes: set[str]) -> None:
    """Svůj výdaj upraví ten, kdo ho zadal; cizí jen správce vozidla.
    Řidič, který zapomněl doklad, si ho má umět doplnit sám."""
    if expense.created_by == actor.id:
        return
    if MANAGE_ANY in codes:
        return
    if MANAGE_OWN in codes and expense.vehicle.responsible_user_id == actor.id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tento výdaj není váš.")
