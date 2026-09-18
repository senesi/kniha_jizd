"""Nastavení aplikace - prahy upozornění (zadání 7/19/20).

Formulář posílá přesně klíče ze SETTING_DEFS; cokoliv jiného
app_settings.set_values ignoruje, takže přes tenhle formulář nejde do
tabulky nastavení propašovat vlastní klíč.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings, flash, mailer
from app.core.audit import log_action
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


# --- nastavení pošty (zadání 20) --------------------------------------

async def _smtp_context(db: AsyncSession) -> dict:
    smtp = await app_settings.get_smtp(db)
    return {
        "smtp": smtp,
        # Heslo se do formuláře nikdy nevrací. Obrazovka smí vědět jen
        # to, jestli nějaké uložené je.
        "smtp_password_set": await app_settings.has_stored_smtp_password(db),
    }


@router.get("/settings/mail")
async def mail_settings_form(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "settings_mail.html", user, db, error=None, **await _smtp_context(db),
    )


@router.post("/settings/mail", dependencies=[Depends(verify_csrf)])
async def mail_settings_save(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()

    def value(name: str) -> str:
        raw = form.get(name)
        return str(raw).strip() if raw is not None else ""

    port = value("smtp_port") or "587"
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        return await render_page(
            request, "settings_mail.html", user, db, status_code=400,
            error="Port musí být číslo mezi 1 a 65535.", **await _smtp_context(db),
        )

    # Prázdné pole hesla znamená „nech, co tam je" - formulář heslo nikdy
    # nevypisuje, takže by ho jinak při každém uložení smazal.
    password_raw = form.get("smtp_password")
    password = str(password_raw) if password_raw not in (None, "") else None

    await app_settings.set_smtp(
        db, host=value("smtp_host"), port=port, user=value("smtp_user"),
        password=password, sender=value("smtp_from"),
        starttls=form.get("smtp_starttls") is not None, actor_id=user.id,
    )
    await log_action(
        db, user_id=user.id, action="update", module="settings", entity_type="smtp",
        # Heslo ani jeho délka do auditu nepatří; stačí, že se měnilo.
        after_data={"host": value("smtp_host"), "port": int(port), "user": value("smtp_user"),
                    "from": value("smtp_from"), "starttls": form.get("smtp_starttls") is not None,
                    "password_changed": password is not None},
    )
    await db.commit()
    return flash.redirect("/kniha-jizd/settings/mail", "settings_saved")


@router.post("/settings/mail/test", dependencies=[Depends(verify_csrf)])
async def mail_settings_test(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    """Zkušební e-mail **na vlastní adresu přihlášeného administrátora**.

    Adresa se nebere z formuláře schválně: jinak by šlo přes přihlášenou
    administraci rozesílat poštu komukoliv."""
    smtp = await app_settings.get_smtp(db)
    error = await mailer.send_mail(
        smtp, user.email, "Kniha jízd – zkušební e-mail",
        "Tohle je zkušební zpráva z Knihy jízd.\n\n"
        "Pokud vám dorazila, odesílání pošty funguje a upozornění na termíny "
        "i rezervace budou chodit.\n",
    )
    if error:
        return await render_page(
            request, "settings_mail.html", user, db, status_code=400,
            error=f"Zkušební e-mail se nepodařilo odeslat: {error}", **await _smtp_context(db),
        )
    return flash.redirect("/kniha-jizd/settings/mail", "test_mail_sent")
