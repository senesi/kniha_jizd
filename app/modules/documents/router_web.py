"""Dokumenty vozidla (Etapa 6, zadání 18).

Stahování je jediná cesta k souboru - adresář s dokumenty není
servírovaný staticky a jména souborů jsou náhodná UUID, takže neexistuje
uhodnutelná URL (zadání 18/30).
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.access import MANAGE_ANY, MANAGE_OWN, assert_vehicle_visible
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, get_user_permission_codes, require_any_permission
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import DOCUMENT_TYPES, Vehicle, VehicleDocument
from app.modules.documents import repository, service
from app.modules.vehicles import repository as vehicles_repository

documents_router = APIRouter(tags=["documents-web"])
vehicle_documents_router = APIRouter(tags=["documents-web"])


async def _load_vehicle(db: AsyncSession, vehicle_id: uuid.UUID, *, user: User, codes: set[str]) -> Vehicle:
    vehicle = await vehicles_repository.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vozidlo nebylo nalezeno.")
    assert_vehicle_visible(codes, vehicle, user)
    return vehicle


async def _load_document(db: AsyncSession, document_id: uuid.UUID, *, user: User, codes: set[str]) -> VehicleDocument:
    document = await repository.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dokument nebyl nalezen.")
    # Skryté vozidlo znamená skryté i jeho dokumenty.
    assert_vehicle_visible(codes, document.vehicle, user)
    return document


def _to_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# --- seznam dokumentů -------------------------------------------------

@vehicle_documents_router.get("/vehicles/{vehicle_id}/documents")
async def vehicle_documents(
    request: Request,
    vehicle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    return await render_page(
        request, "documents_list.html", user, db,
        vehicle=vehicle,
        documents=await repository.list_for_vehicle(db, vehicle.id),
        doc_types=DOCUMENT_TYPES,
        can_manage=service.can_manage_documents(vehicle, user, codes),
        docs=service,
    )


# --- nahrání ----------------------------------------------------------

@vehicle_documents_router.post("/vehicles/{vehicle_id}/documents", dependencies=[Depends(verify_csrf)])
async def document_upload(
    request: Request,
    vehicle_id: uuid.UUID,
    doc_type: str = Form(...),
    title: str = Form(...),
    valid_from: str = Form(""),
    valid_to: str = Form(""),
    note: str = Form(""),
    document: UploadFile = File(...),
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    vehicle = await _load_vehicle(db, vehicle_id, user=user, codes=codes)
    if not service.can_manage_documents(vehicle, user, codes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dokumenty vozidla spravuje jen odpovědná osoba nebo administrátor.",
        )

    data = await document.read()
    try:
        await service.add_document(
            db, vehicle=vehicle, actor=user, doc_type=doc_type, title=title,
            valid_from=_to_date(valid_from), valid_to=_to_date(valid_to), note=note,
            filename=document.filename or "dokument", content_type=document.content_type, data=data,
        )
    except (service.DocumentError, service.DocumentTooLarge, service.UnsupportedDocumentType) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle.id}/documents", "document_added")


# --- stažení ----------------------------------------------------------

@documents_router.get("/documents/{document_id}/file")
async def document_file(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Jediná cesta k souboru.

    Čtení smí každý, kdo vidí vozidlo - řidič zastavený policií
    potřebuje zelenou kartu (viz modules/documents/service.py). Soubor se
    posílá `inline`, aby se PDF otevřelo v prohlížeči telefonu a nemusel
    se stahovat."""
    codes = await get_user_permission_codes(db, user.id)
    document = await _load_document(db, document_id, user=user, codes=codes)

    path = service.file_path(document)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Soubor dokumentu chybí na disku.")

    # Jméno v hlavičce se sestavuje z ASCII varianty i z UTF-8 (RFC 5987),
    # aby si prohlížeč poradil s diakritikou v názvu.
    safe_name = document.original_filename.encode("ascii", "replace").decode("ascii")
    return Response(
        content=path.read_bytes(),
        media_type=document.mime_type,
        headers={
            "Content-Disposition": (
                f'inline; filename="{safe_name}"; '
                f"filename*=UTF-8''{document.original_filename}"
            ),
            # private: dokumenty nesmí skončit ve sdílené cache proxy.
            "Cache-Control": "private, max-age=300",
        },
    )


# --- smazání ----------------------------------------------------------

@documents_router.post("/documents/{document_id}/delete", dependencies=[Depends(verify_csrf)])
async def document_delete(
    document_id: uuid.UUID,
    user: User = Depends(require_any_permission(MANAGE_ANY, MANAGE_OWN)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    document = await _load_document(db, document_id, user=user, codes=codes)
    if not service.can_manage_documents(document.vehicle, user, codes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění k tomuto dokumentu.")

    vehicle_id = document.vehicle_id
    await service.delete_document(db, document=document, actor=user)
    return flash.redirect(f"/kniha-jizd/vehicles/{vehicle_id}/documents", "document_deleted")
