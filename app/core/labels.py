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
    "vymena_oleje": "Výměna oleje",
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
    "oil": "Výměna oleje",
}

ROLE: dict[str, str] = {
    "admin": "Administrátor",
    "odpovedna_osoba": "Odpovědná osoba",
    "user": "Řidič",
}
