"""Dokumenty vozidla - TP, OTP, zelená karta, pojistka (zadání 18).

Kdo je smí **vidět**: každý, kdo vidí vozidlo. Kdo je smí **nahrávat a
mazat**: jen správce vozidla.

Tohle je záměrná změna proti původnímu návrhu, kde bylo i čtení za
správcovským oprávněním. Zelená karta a technický průkaz jsou přesně to,
co řidič potřebuje v ruce při kontrole nebo nehodě - kdyby se k nim
nedostal, funkce by v terénu byla k ničemu. Zadání 4 běžnému uživateli
zakazuje dokumenty *měnit*, ne je vidět.

Soubory nejsou nikdy servírované staticky: leží pod náhodnými UUID jmény
v adresáři mimo dosah webserveru a každé stažení projde autorizovanou
routou (zadání 18/30 - žádná uhodnutelná URL).
"""
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import previews
from app.core.audit import log_action
from app.core.config import get_settings
from app.core.documents import (
    DocumentTooLarge,
    UnsupportedDocumentType,
    save_document_file,
    validate_document,
)
from app.core.access import can_manage_vehicle, MANAGE_ANY, MANAGE_OWN
from app.models.core import User
from app.models.fleet import ALL_DOCUMENT_TYPES, DOCUMENT_TYPES, Vehicle, VehicleDocument
from app.modules.documents import repository

MODULE = "documents"

__all__ = [
    "DocumentError", "DocumentTooLarge", "UnsupportedDocumentType",
    "add_document", "delete_document", "can_manage_documents",
    "load_preview",
]


class DocumentError(Exception):
    pass


def can_manage_documents(vehicle: Vehicle, actor: User, codes: set[str]) -> bool:
    """Nahrávat a mazat smí správce vozidla, ne každý, kdo ho vidí.

    Deleguje se na `can_manage_vehicle`, aby „správce vozidla" znamenal
    všude totéž - včetně vlastníka soukromého vozidla. Dřív tu byla
    vlastní kopie té úvahy a vlastník si ke svému autu nemohl nahrát
    technický průkaz."""
    return can_manage_vehicle(codes, vehicle, actor)


async def add_document(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, doc_type: str, title: str,
    valid_from: date | None, valid_to: date | None, note: str | None,
    filename: str, content_type: str | None, data: bytes,
    service_id: uuid.UUID | None = None, expense_id: uuid.UUID | None = None,
    commit: bool = True,
) -> VehicleDocument:
    """`service_id` / `expense_id` dělají z dokumentu doklad k servisnímu
    záznamu nebo výdaji. Takový dokument se nezobrazuje v seznamu papírů
    vozidla, ale u svého záznamu - viz repository.list_for_vehicle.

    `commit=False` umožňuje uložit doklad ve stejné transakci jako záznam,
    ke kterému patří."""
    if doc_type not in ALL_DOCUMENT_TYPES:
        raise DocumentError("Vyberte typ dokumentu.")
    if not title.strip():
        raise DocumentError("Název dokumentu je povinný.")
    if valid_from and valid_to and valid_to < valid_from:
        raise DocumentError("Platnost „do“ nemůže být dřív než „od“.")
    if not data:
        raise DocumentError("Vyberte soubor.")

    # Ověří příponu i magické bajty - obsah musí odpovídat tomu, co soubor
    # o sobě tvrdí (app/core/documents.py).
    ext, mime_type = validate_document(filename, data)
    stored_filename = save_document_file(get_settings().documents_path, ext, data)

    document = VehicleDocument(
        vehicle_id=vehicle.id,
        doc_type=doc_type,
        title=title.strip(),
        stored_filename=stored_filename,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        valid_from=valid_from,
        valid_to=valid_to,
        note=(note or "").strip() or None,
        uploaded_by=actor.id,
        service_id=service_id,
        expense_id=expense_id,
    )
    db.add(document)
    await db.flush()

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="document",
        entity_id=str(document.id),
        after_data={
            "vehicle_id": str(vehicle.id), "doc_type": doc_type, "title": document.title,
            "original_filename": filename, "size_bytes": len(data),
            "service_id": str(service_id) if service_id else None,
            "expense_id": str(expense_id) if expense_id else None,
        },
    )
    if commit:
        await db.commit()
        return await repository.get(db, document.id)
    return document


async def delete_document(db: AsyncSession, *, document: VehicleDocument, actor: User) -> None:
    """Soft delete. Soubor na disku zůstává - dokument mohl být podkladem
    pro něco, co se ještě řeší, a tichý úklid disku není důvod přijít o
    doklad. Nedostupný je hned: stahovací routa soft-smazaný dokument
    nenajde."""
    document.deleted_at = datetime.now(timezone.utc)
    await log_action(
        db, user_id=actor.id, action="delete", module=MODULE, entity_type="document",
        entity_id=str(document.id), before_data={"title": document.title},
    )
    await db.commit()


def file_path(document: VehicleDocument) -> Path:
    return get_settings().documents_path / document.stored_filename


def preview_path(document: VehicleDocument) -> Path:
    """Miniatura leží vedle originálu, pod stejným náhodným jménem.

    Tedy taky mimo dosah webserveru - náhled technického průkazu je
    citlivý úplně stejně jako on sám a musí projít toutéž autorizovanou
    routou."""
    stem = Path(document.stored_filename).stem
    return get_settings().documents_path / f"{stem}_preview.jpg"


def load_preview(document: VehicleDocument) -> bytes | None:
    """JPEG miniatura prvního listu; `None`, když nejde vyrobit.

    Vyrábí se až při prvním zobrazení a pak zůstane na disku. Dokumenty
    nahrané dřív tak náhled dostanou taky - bez migrace a bez dávkového
    přepočtu."""
    cached = preview_path(document)
    if cached.is_file():
        return cached.read_bytes()

    source = file_path(document)
    if not source.is_file():
        return None
    data = previews.render_preview(source)
    if data is None:
        return None

    # Přes dočasný soubor a os.replace: dva souběžné požadavky na týž
    # dokument jinak zapisují do jednoho souboru naráz a druhý si přečte
    # půlku JPEGu.
    tmp = cached.with_name(f"{cached.stem}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, cached)
    except OSError:
        # Plný disk nebo práva - miniatura se příště zkusí znovu,
        # zobrazení tím padnout nesmí.
        tmp.unlink(missing_ok=True)
    return data


def is_expired(document: VehicleDocument, today: date | None = None) -> bool:
    if document.valid_to is None:
        return False
    return document.valid_to < (today or date.today())


def expires_soon(document: VehicleDocument, *, within_days: int = 30, today: date | None = None) -> bool:
    if document.valid_to is None:
        return False
    today = today or date.today()
    return 0 <= (document.valid_to - today).days <= within_days
