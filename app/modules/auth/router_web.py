from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.db import get_db
from app.core.templates import templates
from app.modules.auth.service import authenticate

router = APIRouter(tags=["auth-web"])

# Where to go after login. Set when an unauthenticated request hit a real
# page first - typically a QR scan at the vehicle (/kniha-jizd/v/<token>),
# which is exactly the moment a driver must not be dumped on a dashboard
# and left to find the vehicle again by hand.
NEXT_SESSION_KEY = "post_login_next"


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
        db, user_id=user.id, action="login", module="auth", entity_type="user", entity_id=str(user.id),
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return RedirectResponse(url=target, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/kniha-jizd/login", status_code=303)
