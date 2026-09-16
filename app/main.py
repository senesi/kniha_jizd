from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.templates import templates
from app.modules.approvals.router_web import approvals_router, vehicle_approvals_router
from app.modules.auth.router_web import router as auth_web_router
from app.modules.defects.router_web import defects_router, vehicle_defects_router
from app.modules.dashboard.router_web import router as dashboard_web_router
from app.modules.notifications.router_web import router as notifications_router
from app.modules.reservations.router_web import reservations_router, vehicle_reservations_router
from app.modules.settings.router_web import router as settings_router
from app.modules.trips.router_web import trips_router, vehicle_trips_router
from app.modules.users.router_web import account_router, users_router
from app.modules.vehicles.router_web import attachments_router, qr_landing_router, vehicles_router

settings = get_settings()

app = FastAPI(title="Kniha jízd", version="0.1.0")

# Own session cookie name/secret - must never collide with DSS's
# "dss_session" or Evidence nářadí's "naradi_session". Independent apps on
# one shared domain: own auth, no shared session, no shared state.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    session_cookie="kniha_jizd_session",
    https_only=settings.environment == "production",
    same_site="lax",
)


@app.exception_handler(HTTPException)
async def error_handler(request: Request, exc: HTTPException):
    """Login (401) redirects. Every other business/validation error
    (400/403/404/409/...) renders a small friendly error page instead of a
    bare JSON body - a real browser user who just submitted a form needs a
    way back, not a JSON dump. /api/ is kept on JSON for forward
    compatibility.

    Registered on Starlette's own HTTPException, not FastAPI's subclass, so
    framework-internal errors (a mistyped URL -> unmatched route) are
    caught too."""
    is_api = request.url.path.startswith("/api/")
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not is_api:
        # Řidič, který načte QR u auta a teprve pak se přihlásí, se musí
        # vrátit na TO vozidlo, ne na dashboard. Předává se jen cesta
        # (?next=), a login ji ještě sám ověří proti /kniha-jizd/ prefixu.
        if request.method == "GET" and request.url.path.startswith("/kniha-jizd/"):
            return RedirectResponse(url=f"/kniha-jizd/login?next={quote(request.url.path, safe='/')}")
        return RedirectResponse(url="/kniha-jizd/login")
    if is_api:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    referer = request.headers.get("referer", "")
    back_url = referer if referer.startswith(str(request.base_url)) else "/kniha-jizd/"
    return templates.TemplateResponse(
        request, "error.html", {"status_code": exc.status_code, "detail": exc.detail, "back_url": back_url},
        status_code=exc.status_code,
    )


@app.get("/healthz", tags=["ops"])
async def healthz():
    return {"status": "ok"}


# Deployed at solareg.azunimb.cz/kniha-jizd (nginx proxies the path through
# unstripped, exactly like Evidence nářadí's /naradi). Every route,
# redirect and template href is written with this prefix baked in literally
# - the same convention the other SOLAREG apps use - so the app behaves
# identically locally and in production, differing only in port/domain.
# /healthz stays unprefixed: only the docker healthcheck hits it, from
# inside the container, never through nginx.
app.include_router(auth_web_router, prefix="/kniha-jizd")
app.include_router(dashboard_web_router, prefix="/kniha-jizd")
app.include_router(vehicles_router, prefix="/kniha-jizd")
app.include_router(attachments_router, prefix="/kniha-jizd")
app.include_router(qr_landing_router, prefix="/kniha-jizd")
app.include_router(vehicle_trips_router, prefix="/kniha-jizd")
app.include_router(trips_router, prefix="/kniha-jizd")
app.include_router(vehicle_reservations_router, prefix="/kniha-jizd")
app.include_router(reservations_router, prefix="/kniha-jizd")
app.include_router(vehicle_defects_router, prefix="/kniha-jizd")
app.include_router(defects_router, prefix="/kniha-jizd")
app.include_router(vehicle_approvals_router, prefix="/kniha-jizd")
app.include_router(approvals_router, prefix="/kniha-jizd")
app.include_router(notifications_router, prefix="/kniha-jizd")
app.include_router(settings_router, prefix="/kniha-jizd")
app.include_router(users_router, prefix="/kniha-jizd")
app.include_router(account_router, prefix="/kniha-jizd")
# Další moduly (servis, dokumenty, kniha
# jízd) se registrují tady, jakmile vzniknou - viz etapy v
# zadání_projektu.md, kapitola 35. Aplikace zůstává spustitelná po každé
# etapě, ne až na konci.

# Vendored, self-hosted JS (the qr-scanner library used by scan_qr.html) -
# no external CDN dependency for an in-field feature, no app data served
# from here, no auth needed.
app.mount("/kniha-jizd/static", StaticFiles(directory="app/static"), name="static")
