import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.db import get_db
from app.core.templates import templates
from app.modules.auth.service import authenticate

router = APIRouter(tags=["auth-web"])

# Technické údaje k přihlášení. Zapisuje se jen to, co je potřeba k
# auditu: kdo (nebo o koho se pokusil), kdy, odkud a s jakým výsledkem.
# Heslo, jeho délka ani obsah session se do auditu nikdy nedostanou
# (app/core/audit.py:scrub je druhá pojistka).
AUTH_MODULE = "auth"

# Where to go after login. Set when an unauthenticated request hit a real
# page first - typically a QR scan at the vehicle (/kniha-jizd/v/<token>),
# which is exactly the moment a driver must not be dumped on a dashboard
# and left to find the vehicle again by hand.
NEXT_SESSION_KEY = "post_login_next"


def _request_facts(request: Request) -> dict:
    """IP a prohlížeč - jediné technické údaje, které se u přihlášení
    ukládají. Za reverzní proxy je skutečná adresa klienta v
    request.client díky --proxy-headers (viz docker/Dockerfile)."""
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _safe_next(raw: str | None) -> str | None:
    """Only ever an in-app absolute path - never an absolute URL or a
    protocol-relative one, so the login form can't be turned into an open
    redirect."""
    if not raw or not raw.startswith("/kniha-jizd/") or raw.startswith("//"):
        return None
    return raw


@router.get("/login")
async def login_form(request: Request, next: str = ""):
    target = _safe_next(next)
    if target:
        request.session[NEXT_SESSION_KEY] = target
    return templates.TemplateResponse(request, "login.html", {"error": None, "next": target or ""})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate(db, email, password)
    if user is None:
        # Neúspěšný pokus se zaznamenává taky - bez toho by nešlo poznat,
        # že někdo zkouší hesla. Ukládá se zadaný e-mail, ne heslo:
        # e-mail je přihlašovací jméno, tedy identifikátor, ne tajemství.
        await log_action(
            db, user_id=None, action="login_failed", module=AUTH_MODULE, entity_type="user",
            description=f"Neúspěšné přihlášení: {email.strip()[:120]}",
            result="failure", **_request_facts(request),
        )
        await db.commit()
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Nesprávný e-mail nebo heslo.", "next": _safe_next(next) or ""},
            status_code=401,
        )

    target = _safe_next(next) or _safe_next(request.session.get(NEXT_SESSION_KEY)) or "/kniha-jizd/"
    # A fresh session for the new identity - keeping the old session dict
    # would carry the previous user's CSRF token and pending redirect over.
    request.session.clear()
    request.session["user_id"] = str(user.id)
    await log_action(
        db, user_id=user.id, action="login", module=AUTH_MODULE, entity_type="user",
        entity_id=str(user.id), description=f"Přihlášení: {user.email}",
        result="success", **_request_facts(request),
    )
    await db.commit()
    return RedirectResponse(url=target, status_code=303)


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    raw_id = request.session.get("user_id")
    request.session.clear()

    # Až po vyčištění session: odhlášení musí proběhnout i tehdy, když se
    # zápis do auditu nepovede.
    if raw_id:
        try:
            user_id = uuid.UUID(str(raw_id))
        except ValueError:
            user_id = None
        if user_id is not None:
            await log_action(
                db, user_id=user_id, action="logout", module=AUTH_MODULE, entity_type="user",
                entity_id=str(user_id), description="Odhlášení", result="success",
                **_request_facts(request),
            )
            await db.commit()

    return RedirectResponse(url="/kniha-jizd/login", status_code=303)
