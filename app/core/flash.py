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
    "trip_times_edited": "Časy jízdy byly opraveny.",
    "approval_requested": "Žádost byla odeslána ke schválení.",
    "approval_approved": "Žádost byla schválena.",
    "approval_rejected": "Žádost byla zamítnuta.",
    "approval_cancelled": "Žádost byla stažena.",
    "approval_needed": "Toto vozidlo vyžaduje schválení – nejdřív odešlete žádost.",
    "defect_reported": "Závada byla nahlášena.",
    "defect_updated": "Závada byla aktualizována.",
    "fueling_added": "Záznam byl uložen.",
    "fueling_deleted": "Záznam byl smazán.",
    "service_added": "Servisní úkon byl zapsán.",
    "service_deleted": "Servisní úkon byl smazán.",
    "document_added": "Dokument byl nahrán.",
    "document_deleted": "Dokument byl smazán.",
    "wheel_set_added": "Sada kol byla přidána.",
    "wheel_set_updated": "Sada byla upravena.",
    "wheel_set_deleted": "Sada byla vyřazena.",
    "wheels_fitted": "Vozidlo bylo přezuto.",
    "wheels_removed": "Kola byla sundána.",
    "expense_added": "Výdaj byl zapsán.",
    "expense_deleted": "Výdaj byl smazán.",
    "receipt_added": "Doklad byl nahrán.",
}


def resolve(code: str | None) -> str | None:
    return MESSAGES.get(code) if code else None


def redirect(url: str, code: str | None = None) -> RedirectResponse:
    """303, aby se POST po refreshi neodeslal znovu."""
    separator = "&" if "?" in url else "?"
    target = f"{url}{separator}flash={code}" if code else url
    return RedirectResponse(url=target, status_code=303)
