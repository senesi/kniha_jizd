import json
from datetime import date, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash as flash_messages
from app.core import fuel, labels
from app.core.csrf import csrf_field

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_field"] = csrf_field
# Palivo vs. elektřina - šablony se nikdy nerozhodují podle fuel_type
# samy, ptají se těchhle funkcí (app/core/fuel.py).
templates.env.globals["fuel"] = fuel
# České popisky číselníků - {{ labels.VEHICLE_TYPE[...] }}.
templates.env.globals["labels"] = labels


async def render_page(request: Request, name: str, user, db: AsyncSession, *, status_code: int = 200, **context):
    """Renders a base.html-extending page with the app shell's nav flags and
    unread-notification count always populated, computed once from the
    actor's own permission codes. Not a new permission or access rule -
    just making sure the sidebar/bottom nav is consistent on every page
    instead of only the handful of routes that would otherwise compute
    these ad hoc."""
    from app.core.deps import get_user_permission_codes
    from app.modules.notifications import repository as notifications_repository

    codes = await get_user_permission_codes(db, user.id)
    nav = {
        "can_manage_users": "core.user.manage" in codes,
        "can_manage_vehicles": "fleet.vehicle.manage" in codes,
        "can_create_vehicles": "fleet.vehicle.create" in codes,
        "can_view_logbook": "fleet.logbook.view" in codes,
        "can_manage_settings": "fleet.settings.manage" in codes,
        "unread_notifications": await notifications_repository.count_unread(db, user.id),
        # ?flash=<kód> po redirectu z POSTu; neznámý kód se ignoruje.
        "flash": flash_messages.resolve(request.query_params.get("flash")),
    }
    return templates.TemplateResponse(request, name, {"user": user, **nav, **context}, status_code=status_code)


def format_datetime(value) -> str:
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y %H:%M")


def format_date(value) -> str:
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y")


def format_km(value) -> str:
    """Thousands separated by a non-breaking space - "127 480 km" stays on
    one line on a phone."""
    if value is None:
        return "-"
    return f"{int(value):,}".replace(",", " ")


def format_money(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")


def format_liters(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",")


templates.env.filters["dt"] = format_datetime
templates.env.filters["d"] = format_date
templates.env.filters["km"] = format_km
templates.env.filters["money"] = format_money
templates.env.filters["liters"] = format_liters


def initials(full_name: str) -> str:
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


templates.env.filters["initials"] = initials


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def to_json(value) -> Markup:
    """{{ value | tojson }} - plain Jinja2 (unlike Flask's) has no built-in
    tojson, needed to safely embed server data as a JSON <script> payload
    (the reservation calendar's event list). Escapes </script>-breaking
    characters the same way Flask's own tojson does."""
    return Markup(
        json.dumps(value, default=_json_default)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


templates.env.filters["tojson"] = to_json
