import secrets

from fastapi import Form, HTTPException, Request, status
from markupsafe import Markup

CSRF_SESSION_KEY = "csrf_token"


def get_csrf_token(request: Request) -> str:
    """Returns the current session's CSRF token, generating one on first
    use. Session-bound (not per-form), matching the standard
    synchronizer-token pattern for a stateful, cookie-session app."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def csrf_field(request: Request) -> Markup:
    """Jinja global - `request` is already present in every TemplateResponse
    context (Starlette convention), so templates can call
    `{{ csrf_field(request) }}` inside any <form method="post"> without
    every route needing to pass it through its own context dict."""
    token = get_csrf_token(request)
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


async def verify_csrf(request: Request, csrf_token: str = Form(...)) -> None:
    """FastAPI dependency - add to every state-changing (POST) route except
    /kniha-jizd/login (no authenticated session yet to protect, and login
    itself doesn't perform a sensitive state change on someone else's
    behalf). Constant-time compare; a missing or mismatched token is a 403,
    not a silent pass-through."""
    session_token = request.session.get(CSRF_SESSION_KEY)
    if not session_token or not secrets.compare_digest(session_token, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token")
