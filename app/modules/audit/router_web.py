"""Administrace → Audit / Aktivita (zadání 26/30).

**Read-only.** Nejsou tu žádné POST routy, takže přes tuhle obrazovku
nejde auditní záznam změnit ani smazat — ne proto, že by je šablona
schovávala, ale proto, že neexistují.

Přístup má jen `core.audit.view`, tedy administrátor. Kontrola je v
dependency každé routy, ne v šabloně: běžný uživatel se sem nedostane
ani přímou URL.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import require_permission
from app.core.templates import render_page
from app.models.core import User
from app.modules.audit import repository
from app.modules.audit.filters import PAGE_SIZE, AuditFilter

router = APIRouter(tags=["audit-web"])

VIEW = "core.audit.view"


@router.get("/admin/audit")
async def audit_list(
    request: Request,
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    flt = AuditFilter.from_params(request.query_params)
    entries = await repository.list_entries(db, flt)
    total = await repository.count_entries(db, flt)

    return await render_page(
        request, "audit_list.html", user, db,
        filter=flt,
        entries=entries,
        vehicles_by_id=await repository.vehicles_for(db, entries),
        total=total,
        page_size=PAGE_SIZE,
        last_page=max(1, -(-total // PAGE_SIZE)),
        modules=await repository.distinct_modules(db),
        actions=await repository.distinct_actions(db),
        actors=await repository.actors(db),
        audited_vehicles=await repository.audited_vehicles(db),
    )


@router.get("/admin/audit/{entry_id}")
async def audit_detail(
    request: Request,
    entry_id: uuid.UUID,
    user: User = Depends(require_permission(VIEW)),
    db: AsyncSession = Depends(get_db),
):
    entry = await repository.get(db, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Záznam nebyl nalezen.")

    vehicles = await repository.vehicles_for(db, [entry])
    return await render_page(
        request, "audit_detail.html", user, db,
        entry=entry, vehicle=vehicles.get(entry.vehicle_id),
    )
