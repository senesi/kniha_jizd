"""OCR jako pomůcka, nikdy jako závislost (zadání 8/11/25).

Rozhraní je záměrně malé a poskytovatel vyměnitelný. Při
`OCR_PROVIDER=none` (výchozí stav) `read_odometer` vždy vrátí None a
celý tok jízdy funguje dál - uživatel prostě zadá stav km sám. Žádná
cesta v aplikaci nesmí na úspěchu OCR záviset.

Když poskytovatel naopak hodnotu přečte, **nikdy** se neuloží rovnou:
vrátí se do formuláře jako návrh, který uživatel potvrdí nebo přepíše
(viz trips/router_web.py). OCR je vstup pro člověka, ne pro databázi.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date

from app.core.config import get_settings
from app.core.fuel import UNIT_KWH, UNIT_LITERS

logger = logging.getLogger(__name__)

# Stav tachometru mimo tenhle rozsah je skoro jistě špatně přečtený údaj
# (část SPZ, teplota, denní počítadlo), ne skutečný nájezd.
MIN_PLAUSIBLE_KM = 0
MAX_PLAUSIBLE_KM = 2_000_000


@dataclass(frozen=True)
class OdometerReading:
    """Návrh hodnoty k potvrzení uživatelem, ne fakt."""

    value: int
    raw_text: str = ""


def is_configured() -> bool:
    return get_settings().ocr_provider not in ("", "none")


def parse_odometer_text(text: str) -> OdometerReading | None:
    """Z rozpoznaného textu vytáhne nejdelší číslo v rozumném rozsahu.

    Oddělené jako čistá funkce, aby se dala testovat bez jakéhokoliv OCR
    enginu - tohle je ta část, kde vznikají chyby (mezery a tečky mezi
    tisíci, přilepené „km", desetinné denní počítadlo)."""
    if not text:
        return None

    best: int | None = None
    for candidate in re.findall(r"\d[\d\s.,]*", text):
        # "127 480" / "127.480" jsou tisíce, ne desetinná čísla - na
        # tachometru se zlomky kilometru neevidují.
        digits = re.sub(r"[^\d]", "", candidate)
        if not digits:
            continue
        value = int(digits)
        if not (MIN_PLAUSIBLE_KM <= value <= MAX_PLAUSIBLE_KM):
            continue
        # Nejdelší číslo: "127480 km" má přednost před "5" z popisku.
        if best is None or len(digits) > len(str(best)):
            best = value

    return OdometerReading(value=best, raw_text=text.strip()) if best is not None else None


async def read_odometer(image_bytes: bytes) -> OdometerReading | None:
    """Pokus o přečtení stavu tachometru z fotografie.

    Vrací None, kdykoliv OCR není nakonfigurované, selže nebo si není
    jisté. Nikdy nevyhazuje výjimku - výpadek OCR nesmí shodit zahájení
    ani ukončení jízdy."""
    if not is_configured():
        return None
    try:
        return await _read_with_provider(image_bytes)
    except Exception as exc:  # noqa: BLE001 - viz docstring modulu
        logger.warning("OCR tachometru selhalo: %s", exc)
        return None


async def _read_with_provider(image_bytes: bytes) -> OdometerReading | None:
    """Napojení na konkrétní engine (Etapa 5).

    Až sem přibude tesseract nebo jiný poskytovatel, musí platit totéž:
    vrátit návrh, nebo None. Nic víc."""
    provider = get_settings().ocr_provider
    logger.info("OCR poskytovatel %r zatím není implementovaný", provider)
    return None


# --- účtenka za tankování / nabíjení (zadání 15/25) -------------------

@dataclass(frozen=True)
class ReceiptReading:
    """Návrh údajů z účtenky. Žádné pole není povinné - OCR přečte, co
    přečte, a zbytek doplní uživatel. Nikdy se neukládá bez potvrzení."""

    fueled_at: date | None = None
    quantity: float | None = None
    unit: str | None = None
    price_total_czk: float | None = None
    price_per_unit_czk: float | None = None
    station: str | None = None
    raw_text: str = ""

    @property
    def has_anything(self) -> bool:
        return any(
            value is not None
            for value in (
                self.fueled_at, self.quantity, self.price_total_czk,
                self.price_per_unit_czk, self.station,
            )
        )


def _czech_number(text: str) -> float | None:
    """„48,50" i „48.50" i „1 234,56" - české účtenky používají obojí a
    tisíce oddělují mezerou."""
    cleaned = text.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def _find_date(text: str) -> date | None:
    """Datum v českém formátu (1.2.2026, 01. 02. 2026) nebo ISO."""
    czech = re.search(r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})\b", text)
    if czech:
        day, month, year = (int(part) for part in czech.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return date(*(int(part) for part in iso.groups()))
        except ValueError:
            return None
    return None


def parse_receipt_text(text: str) -> ReceiptReading | None:
    """Vytáhne z rozpoznaného textu, co jde. Oddělené jako čistá funkce -
    právě tady vznikají chyby (desetinná čárka, mezera v tisících,
    jednotka přilepená k číslu), takže se to musí dát testovat bez
    jakéhokoliv OCR enginu."""
    if not text or not text.strip():
        return None

    # Jednotka rozhoduje, jak číst množství. kWh se hledá první - "kWh"
    # obsahuje "h", ne "l", takže se nepoplete s litry.
    unit = None
    quantity = None

    kwh = re.search(r"(\d+(?:[.,]\d+)?)\s*kwh\b", text, re.IGNORECASE)
    if kwh:
        unit, quantity = UNIT_KWH, _czech_number(kwh.group(1))
    else:
        liters = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:l|litr\w*)\b", text, re.IGNORECASE)
        if liters:
            unit, quantity = UNIT_LITERS, _czech_number(liters.group(1))

    # Celková cena: hledá se u slova, ne jen "největší číslo" - na účtence
    # bývá i číslo karty nebo IČO.
    total = None
    total_match = re.search(
        r"(?:celkem|k\s*[úu]hrad[ěe]|celkov[áa]\s*cena)\D{0,20}(\d+(?:[ .,]\d+)*)",
        text, re.IGNORECASE,
    )
    if total_match:
        total = _czech_number(total_match.group(1))

    per_unit = None
    # "Kč" i "Kc" i "CZK" - tiskárny na pumpách často diakritiku neumí.
    per_unit_match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:kč|kc|czk)\s*/\s*(?:l|kwh)", text, re.IGNORECASE,
    )
    if per_unit_match:
        per_unit = _czech_number(per_unit_match.group(1))

    reading = ReceiptReading(
        fueled_at=_find_date(text),
        quantity=quantity,
        unit=unit,
        price_total_czk=total,
        price_per_unit_czk=per_unit,
        raw_text=text.strip(),
    )
    return reading if reading.has_anything else None


async def read_receipt(image_bytes: bytes) -> ReceiptReading | None:
    """Pokus o přečtení účtenky. Stejná pravidla jako u tachometru: vrací
    None, kdykoliv OCR není k dispozici, selže nebo nic nenajde, a nikdy
    nevyhazuje výjimku."""
    if not is_configured():
        return None
    try:
        text = await _extract_text(image_bytes)
    except Exception as exc:  # noqa: BLE001 - viz docstring modulu
        logger.warning("OCR účtenky selhalo: %s", exc)
        return None
    return parse_receipt_text(text or "")


async def _extract_text(image_bytes: bytes) -> str | None:
    """Napojení na engine (Etapa 5+). Až sem přibude tesseract, musí
    platit totéž: vrátit text, nebo None."""
    provider = get_settings().ocr_provider
    logger.info("OCR poskytovatel %r zatím není implementovaný", provider)
    return None
