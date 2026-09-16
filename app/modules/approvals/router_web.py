"""Žádosti o použití vozidla (požadavek B).

Dvě strany téhož: žadatel vidí stav svých žádostí, schvalovatel seznam
těch, o kterých má rozhodnout.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.access import assert_vehicle_visible, sees_every_vehicle
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import TripRequest
from app.modules.approvals import repository, service
from app.modules.reservations.calendar import LOCAL_TZ
from app.modules.vehicles import repository as vehicles_repository

approvals_router = APIRouter(tags=["approvals-web"])
vehicle_approvals_router = APIRouter(tags=["approvals-web"])


def _parse_local(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        return None
    return naive.replace(tzinfo=LOCAL_TZ) if naive.tzinfo is None else naive


async def _load(db: AsyncSession, request_id: uuid.UUID) -> TripRequest:
    trip_request = await repository.get(db, request_id)
    if trip_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Žádost nebyla nalezena.")
    return trip_request


# --- žádost o vozidlo -------------------------------------------------

@vehicle_approvals_router.post("/vehicles/{vehicle_id}/request", dependencies=[Depends(verify_csrf)])
async def request_vehicle(
    vehicle_id: uuid.UUID,
    purpose: str = Form(""),
    needed_from: str = Form(""),
    needed_to: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    codes = await get_user_permission_codes(db, user.id)
    assert_vehicle_visible(codes, vehicle, user)

    try:
        trip_request = await service.create_request(
            db, vehicle=vehicle, actor=user, purpose=purpose,
            needed_from=_parse_local(needed_from), needed_to=_parse_local(needed_to),
        )
    except service.ApprovalError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect(f"/kniha-jizd/approvals/{trip_request.id}", "approval_requested")


# --- přehledy ---------------------------------------------------------
# Pozor na pořadí: /approvals/mine i /approvals/pending musí být
# registrované PŘED /approvals/{request_id}.

@approvals_router.get("/approvals/mine")
async def my_requests(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "approvals_mine.html", user, db,
        requests=await repository.list_for_requester(db, user.id),
    )


@approvals_router.get("/approvals/pending")
async def pending_requests(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    return await render_page(
        request, "approvals_pending.html", user, db,
        requests=await repository.list_pending_for_decider(
            db, user_id=user.id, sees_all=sees_every_vehicle(codes),
        ),
    )


@approvals_router.get("/approvals/{request_id}")
async def request_detail(
    request: Request,
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip_request = await _load(db, request_id)
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_view_request(trip_request, user, codes):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Žádost nebyla nalezena.")

    return await render_page(
        request, "approval_detail.html", user, db,
        trip_request=trip_request,
        can_decide=service.can_decide(trip_request.vehicle, user, codes),
        is_mine=trip_request.requester_id == user.id,
    )


# --- rozhodnutí -------------------------------------------------------

@approvals_router.post("/approvals/{request_id}/decide", dependencies=[Depends(verify_csrf)])
async def decide_request(
    request_id: uuid.UUID,
    decision: str = Form(...),
    note: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip_request = await _load(db, request_id)
    codes = await get_user_permission_codes(db, user.id)
    if not service.can_decide(trip_request.vehicle, user, codes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="O této žádosti rozhoduje odpovědná osoba vozidla nebo administrátor.",
        )
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Neplatné rozhodnutí.")

    try:
        await service.decide(
            db, request=trip_request, actor=user, approve=(decision == "approve"), note=note,
        )
    except service.ApprovalError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    code = "approval_approved" if decision == "approve" else "approval_rejected"
    return flash.redirect(f"/kniha-jizd/approvals/{trip_request.id}", code)


@approvals_router.post("/approvals/{request_id}/cancel", dependencies=[Depends(verify_csrf)])
async def cancel_request(
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trip_request = await _load(db, request_id)
    codes = await get_user_permission_codes(db, user.id)
    # Stáhnout žádost smí žadatel; administrátor navíc jako úklid.
    if trip_request.requester_id != user.id and not service.can_decide(trip_request.vehicle, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tato žádost není vaše.")

    try:
        await service.cancel_request(db, request=trip_request, actor=user)
    except service.ApprovalError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return flash.redirect("/kniha-jizd/approvals/mine", "approval_cancelled")
