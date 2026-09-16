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

from app.core.config import get_settings

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
