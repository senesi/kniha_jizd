"""Nastavení aplikace - prahy upozornění (zadání 7/19/20).

Formulář posílá přesně klíče ze SETTING_DEFS; cokoliv jiného
app_settings.set_values ignoruje, takže přes tenhle formulář nejde do
tabulky nastavení propašovat vlastní klíč.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings, flash
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import require_permission
from app.core.templates import render_page
from app.models.core import User

router = APIRouter(tags=["settings-web"])

MANAGE = "fleet.settings.manage"


@router.get("/settings")
async def settings_form(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "settings.html", user, db,
        definitions=app_settings.SETTING_DEFS, values=await app_settings.get_all(db), error=None,
    )


@router.post("/settings", dependencies=[Depends(verify_csrf)])
async def settings_save(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    values: dict[str, int] = {}
    for definition in app_settings.SETTING_DEFS:
        raw = form.get(definition.key)
        if raw is None or not str(raw).strip():
            continue
        try:
            parsed = int(str(raw))
        except ValueError:
            return await render_page(
                request, "settings.html", user, db, status_code=400,
                definitions=app_settings.SETTING_DEFS, values=await app_settings.get_all(db),
                error=f"„{definition.label}“ musí být celé číslo.",
            )
        if parsed < 0:
            return await render_page(
                request, "settings.html", user, db, status_code=400,
                definitions=app_settings.SETTING_DEFS, values=await app_settings.get_all(db),
                error=f"„{definition.label}“ nemůže být záporné.",
            )
        values[definition.key] = parsed

    await app_settings.set_values(db, values, user.id)
    await db.commit()
    return flash.redirect("/kniha-jizd/settings", "settings_saved")
