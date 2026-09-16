"""Jednorázová potvrzovací hláška po redirectu (POST -> 303 -> GET).

V URL se předává jen KÓD, nikdy text hlášky. Kdyby se do stránky
vypisoval text z query stringu, dal by se uživateli poslat odkaz, který
mu v aplikaci zobrazí libovolnou větu ("Vaše heslo vypršelo, zavolejte
na...") - Jinja to sice escapuje, takže nejde o XSS, ale jako nástroj pro
sociální inženýrství by to stačilo. Neznámý kód se prostě nezobrazí.
"""
from fastapi.responses import RedirectResponse

MESSAGES: dict[str, str] = {
    "vehicle_created": "Vozidlo bylo založeno.",
    "vehicle_updated": "Změny vozidla byly uloženy.",
    "odometer_corrected": "Stav tachometru byl administrativně opraven.",
    "photo_added": "Fotografie byla nahrána.",
    "photo_deleted": "Fotografie byla odstraněna.",
    "settings_saved": "Nastavení bylo uloženo.",
    "user_created": "Uživatel byl založen.",
    "user_updated": "Změny uživatele byly uloženy.",
    "password_changed": "Heslo bylo změněno.",
    "password_reset": "Heslo uživatele bylo nastaveno.",
    "profile_updated": "Profil byl uložen.",
    "notifications_read": "Upozornění byla označena jako přečtená.",
    "trip_started": "Výpůjčka byla zahájena. Šťastnou cestu!",
    "trip_ended": "Jízda byla uzavřena a zapsána do knihy jízd.",
    "trip_cancelled": "Jízda byla zrušena.",
    "driver_added": "Další řidič byl přidán.",
    "driver_removed": "Řidič byl od jízdy odebrán.",
    "note_added": "Poznámka byla přidána.",
    "reservation_created": "Rezervace byla vytvořena.",
    "reservation_updated": "Rezervace byla upravena.",
    "reservation_cancelled": "Rezervace byla zrušena.",
}


def resolve(code: str | None) -> str | None:
    return MESSAGES.get(code) if code else None


def redirect(url: str, code: str | None = None) -> RedirectResponse:
    """303, aby se POST po refreshi neodeslal znovu."""
    separator = "&" if "?" in url else "?"
    target = f"{url}{separator}flash={code}" if code else url
    return RedirectResponse(url=target, status_code=303)
