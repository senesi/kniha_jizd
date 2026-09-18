"""České popisky číselníků z app/models/fleet.py.

V databázi jsou stabilní kódy ("pracovni_stroj"), uživatel vidí text
("Pracovní stroj"). Překlad je tady, ne v šablonách - jinak by se stejný
kód psal jinak v seznamu, v detailu a v exportu.
"""
VEHICLE_TYPE: dict[str, str] = {
    "osobni": "Osobní",
    "dodavka": "Dodávka",
    "nakladni": "Nákladní",
    "pracovni_stroj": "Pracovní stroj",
    "prives": "Přívěs",
    "jine": "Jiné",
}

FUEL_TYPE: dict[str, str] = {
    "nafta": "Nafta",
    "benzin": "Benzín",
    "elektro": "Elektro",
    "hybrid": "Hybrid",
    "lpg": "LPG",
    "cng": "CNG",
    "jine": "Jiné",
}

VEHICLE_STATUS: dict[str, str] = {
    "available": "K dispozici",
    "in_service": "V servisu",
    "blocked": "Mimo provoz",
}

TRIP_STATUS: dict[str, str] = {
    "active": "Probíhá",
    "completed": "Ukončená",
    "cancelled": "Zrušená",
}

TRIP_PURPOSE: dict[str, str] = {
    "servis": "Servis",
    "montaz": "Montáž",
    "doprava_materialu": "Doprava materiálu",
    "schuzka": "Schůzka",
    "sluzebni_cesta": "Služební cesta",
    "jine": "Jiný",
}

RESERVATION_STATUS: dict[str, str] = {
    "active": "Aktivní",
    "cancelled": "Zrušená",
    "fulfilled": "Vyčerpaná",
}

RESERVATION_KIND: dict[str, str] = {
    "reservation": "Rezervace",
    "service": "Servis / mimo provoz",
}

DEFECT_STATUS: dict[str, str] = {
    "new": "Nová",
    "in_progress": "Řeší se",
    "resolved": "Vyřešená",
}

DEFECT_PRIORITY: dict[str, str] = {
    "low": "Nízká",
    "normal": "Běžná",
    "high": "Vysoká",
    "critical": "Kritická",
}

SERVICE_TYPE: dict[str, str] = {
    # Ten úkon, který posouvá hlídaný servisní interval. U spalovacího
    # vozu je to výměna oleje, u elektromobilu prohlídka a náplně -
    # proto obecnější název.
    "vymena_oleje": "Servisní prohlídka / olej",
    "filtry": "Filtry",
    "brzdy": "Brzdy",
    "pneumatiky": "Pneumatiky",
    "oprava": "Oprava",
    "pravidelny_servis": "Pravidelný servis",
    "stk": "STK",
    "jine": "Jiné",
}

DOCUMENT_TYPE: dict[str, str] = {
    "tp": "Technický průkaz",
    "otp": "Osvědčení o registraci (OTP)",
    "zelena_karta": "Zelená karta",
    "pojistka": "Pojistná smlouva",
    "leasing": "Leasing",
    "jine": "Jiné",
    "faktura": "Faktura",
    "doklad": "Doklad",
}

NOTIFICATION_KIND: dict[str, str] = {
    "reservation": "Rezervace",
    "trip_start": "Zahájení výpůjčky",
    "trip_end": "Ukončení výpůjčky",
    "defect": "Závada",
    "service_due": "Blížící se servis",
    "stk": "STK",
    "vignette": "Dálniční známka",
    "insurance": "Pojištění",
    "oil": "Servisní prohlídka",
    "approval_request": "Žádost o schválení",
    "approval_decision": "Rozhodnutí o žádosti",
}

TRIP_REQUEST_STATUS: dict[str, str] = {
    "pending": "Čeká na schválení",
    "approved": "Schváleno",
    "rejected": "Zamítnuto",
    "cancelled": "Staženo",
}

VEHICLE_VISIBILITY: dict[str, str] = {
    "all": "Viditelné všem",
    "restricted": "Jen odpovědná osoba a administrátor",
}

EXPENSE_TYPE: dict[str, str] = {
    "palivo": "Palivo",
    "nabijeni": "Nabíjení",
    "servis": "Servis",
    "pneumatiky": "Pneumatiky",
    "stk": "STK",
    "dalnicni_znamka": "Dálniční známka",
    "pojisteni": "Pojištění",
    "myti": "Mytí",
    "ostatni": "Ostatní",
}

WHEEL_SEASON: dict[str, str] = {
    "summer": "Letní",
    "winter": "Zimní",
}

ROLE: dict[str, str] = {
    "admin": "Administrátor",
    "odpovedna_osoba": "Odpovědná osoba",
    "user": "Řidič",
}


# --- audit ------------------------------------------------------------
# Popisky pro administrátorskou obrazovku auditu. Kód, který tu není, se
# zobrazí tak, jak je - lepší surový kód než prázdno u nové akce.

AUDIT_MODULE: dict[str, str] = {
    "auth": "Přihlášení",
    "approvals": "Schvalování",
    "defects": "Závady",
    "documents": "Dokumenty",
    "expenses": "Výdaje",
    "fuelings": "Tankování / nabíjení",
    "logbook": "Kniha jízd",
    "notifications": "Notifikace",
    "reservations": "Rezervace",
    "services": "Servis",
    "settings": "Nastavení",
    "trips": "Jízdy",
    "users": "Uživatelé",
    "vehicles": "Vozidla",
    "wheels": "Kola a pneumatiky",
}

AUDIT_ACTION: dict[str, str] = {
    "create": "Vytvoření",
    "update": "Změna",
    "delete": "Smazání",
    "cancel": "Zrušení",
    "approve": "Schválení",
    "reject": "Zamítnutí",
    "consume": "Uplatnění",
    "status_change": "Změna stavu",
    "priority_change": "Změna priority",
    "odometer_correction": "Oprava tachometru",
    "trip_start": "Zahájení jízdy",
    "trip_end": "Ukončení jízdy",
    "trip_cancel": "Zrušení jízdy",
    "trip_times_edit": "Oprava časů jízdy",
    "driver_add": "Přidání řidiče",
    "driver_remove": "Odebrání řidiče",
    "note_add": "Přidání poznámky",
    "receipt_add": "Přidání dokladu",
    "fit": "Nasazení kol",
    "remove": "Sundání kol",
    "password_change": "Změna hesla",
    "password_reset": "Reset hesla",
    "login": "Přihlášení",
    "login_failed": "Neúspěšné přihlášení",
    "logout": "Odhlášení",
}
