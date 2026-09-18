"""Kniha jízd a její exporty (Etapa 8, zadání 22/23).

Samostatná hlavní sekce napříč vozidly - na rozdíl od „Mých jízd", které
ukazují jen to, co odjel přihlášený uživatel. Proto je za oprávněním
`fleet.logbook.view`, které má administrátor a odpovědná osoba, ne
každý řidič.

Viditelnost vozidel řeší dotaz (`logbook/repository.py`), ne tenhle
soubor: kdyby se filtrovalo až při vykreslení, do exportu by se skryté
jízdy stejně dostaly.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_user_permission_codes, require_any_permission
from app.core.templates import render_page
from app.models.core import User
from app.models.fleet import TRIP_PURPOSES, TRIP_STATUSES
from app.modules.logbook import export, repository
from app.modules.logbook.filters import PAGE_SIZE, LogbookFilter

router = APIRouter(tags=["logbook-web"])

VIEW = "fleet.logbook.view"

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def _period_label(flt: LogbookFilter) -> str:
    if flt.date_from and flt.date_to:
        return f"{flt.date_from:%d.%m.%Y} – {flt.date_to:%d.%m.%Y}"
    if flt.date_from:
        return f"od {flt.date_from:%d.%m.%Y}"
    if flt.date_to:
        return f"do {flt.date_to:%d.%m.%Y}"
    return "celé období"


# --- výpis ------------------------------------------------------------

@router.get("/logbook")
async def logbook(
    request: Request,
    user: User = Depends(require_any_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    codes = await get_user_permission_codes(db, user.id)
    flt = LogbookFilter.from_params(request.query_params)

    total = await repository.count_trips(db, flt, codes=codes, user=user)
    return await render_page(
        request, "logbook.html", user, db,
        filter=flt,
        trips=await repository.list_trips(db, flt, codes=codes, user=user),
        summary=await repository.summary(db, flt, codes=codes, user=user),
        total=total,
        page_size=PAGE_SIZE,
        # Poslední stránka: u nuly výsledků pořád jedna, aby stránkování
        # nehlásilo „strana 1 z 0".
        last_page=max(1, -(-total // PAGE_SIZE)),
        vehicles=await repository.filter_vehicles(db, codes=codes, user=user),
        drivers=await repository.filter_drivers(db, codes=codes, user=user),
        purposes=TRIP_PURPOSES,
        statuses=TRIP_STATUSES,
        period_label=_period_label(flt),
    )


# --- export -----------------------------------------------------------
#
# Pevný výčet formátů v cestě, ne parametr: neznámá přípona tak skončí
# 404 na úrovni routování a nikdy se nedostane do jména souboru.

@router.get("/logbook/export.{extension}")
async def logbook_export(
    extension: str,
    request: Request,
    user: User = Depends(require_any_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Export respektuje aktivní filtry (zadání 23).

    Bere se **celý** vyfiltrovaný rozsah, ne jen zobrazená stránka -
    kdo si vyfiltruje čtvrtletí, čeká čtvrtletí, ne prvních padesát
    jízd."""
    if extension not in CONTENT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Neznámý formát exportu.")

    codes = await get_user_permission_codes(db, user.id)
    flt = LogbookFilter.from_params(request.query_params)
    trips = await repository.list_trips(db, flt, codes=codes, user=user, paginate=False)
    rows = export.build_rows(trips)

    title = "Kniha jízd"
    subtitle = f"{_period_label(flt)} · {len(rows)} jízd"

    if extension == "csv":
        content = export.to_csv(rows)
    elif extension == "xlsx":
        content = export.to_xlsx(rows, title=title)
    else:
        content = export.to_pdf(rows, title=title, subtitle=subtitle)

    filename = f"kniha-jizd-{date.today():%Y-%m-%d}.{extension}"
    return Response(
        content=content,
        media_type=CONTENT_TYPES[extension],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Kniha jízd je firemní údaj - nikdy do sdílené cache.
            "Cache-Control": "private, no-store",
        },
    )
