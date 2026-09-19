"""OCR jako pomůcka, nikdy jako závislost (zadání 8/11/25).

Rozhraní je záměrně malé a poskytovatel vyměnitelný. Při
`OCR_PROVIDER=none` vrací čtecí funkce vždy None a celý tok funguje dál
- uživatel prostě zadá hodnoty sám. Žádná cesta v aplikaci nesmí na
úspěchu OCR záviset.

Když poskytovatel naopak hodnotu přečte, **nikdy** se neuloží rovnou:
vrátí se do formuláře jako návrh, který uživatel potvrdí nebo přepíše.
OCR je vstup pro člověka, ne pro databázi.

**Čte se účtenka, ne tachometr.** Engine je tesseract, běží v
kontejneru a nic neposílá ven. Na účtenku z termotiskárny (tmavý text
na světlém, rovné řádky) je slušný; na digitální displej za sklem, v
odrazech a nafocený šikmo slabý. Špatně přečtený stav km je přitom
horší než žádný - posouvá tachometr vozidla. Čtení tachometru proto
zůstává vypnuté (`OCR_READ_ODOMETER=false`) a kód pro něj je připravený
na chvíli, kdy bude po ruce engine, který to zvládne.
"""
import asyncio
import io
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


PROVIDER_TESSERACT = "tesseract"

# Účtenka bývá vyfocená z ruky a v malém rozlišení; pod tuhle šířku se
# před čtením zvětší, protože tesseractu drobné písmo výrazně vadí.
MIN_OCR_WIDTH = 1000


def is_configured() -> bool:
    return get_settings().ocr_provider not in ("", "none")


def reads_odometer() -> bool:
    """Čte se i stav tachometru? Viz docstring modulu - zatím ne."""
    return is_configured() and get_settings().ocr_read_odometer


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
    ani ukončení jízdy.

    Ve výchozím nastavení vrací None vždycky: tesseract na fotku
    tachometru nestačí a špatný návrh je tu horší než žádný (viz
    docstring modulu)."""
    if not reads_odometer():
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


#: Znaky, které OCR běžně plete s malým "l" u jednotky litru. Na
#: skutečné účtence tesseract přečetl "48,50 |" a "38,90 Kc/I" - tedy
#: svislítko a velké I. Bez téhle tolerance se množství i cena za litr
#: zahodí, přestože je text jinak přečtený správně.
LITER_LOOKALIKE = r"[l|I1!]"


def parse_receipt_text(text: str) -> ReceiptReading | None:
    """Vytáhne z rozpoznaného textu, co jde. Oddělené jako čistá funkce -
    právě tady vznikají chyby (desetinná čárka, mezera v tisících,
    jednotka přilepená k číslu, zaměněná písmena), takže se to musí dát
    testovat bez jakéhokoliv OCR enginu."""
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
        # Jednotka musí stát samostatně za číslem, ne uvnitř slova -
        # jinak by "48,50 Kc" dalo litry kvůli písmenu v "Kc". Proto
        # mezera před a konec slova za. "1" je mezi záměnami schválně,
        # ale jen s mezerou před sebou, aby se "48,501" nečetlo jako
        # 48,50 l.
        liters = re.search(
            rf"(\d+(?:[.,]\d+)?)\s+{LITER_LOOKALIKE}(?![\w.,])|(\d+(?:[.,]\d+)?)\s*litr\w*\b",
            text, re.IGNORECASE,
        )
        if liters:
            raw = liters.group(1) or liters.group(2)
            unit, quantity = UNIT_LITERS, _czech_number(raw)

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
        rf"(\d+(?:[.,]\d+)?)\s*(?:kč|kc|czk)\s*/\s*(?:kwh|{LITER_LOOKALIKE})",
        text, re.IGNORECASE,
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


def _prepare(image_bytes: bytes):
    """Předzpracování účtenky pro tesseract.

    Tři věci, které na fotce z ruky dělají největší rozdíl: převod do
    šedi (barva jen mate), zvětšení drobného písma a roztažení kontrastu
    - termotisk bývá spíš šedý než černý. Nic chytřejšího tu záměrně
    není; složitější filtry pomáhají na jedné fotce a škodí na druhé."""
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")

    if image.width < MIN_OCR_WIDTH:
        ratio = MIN_OCR_WIDTH / image.width
        image = image.resize(
            (MIN_OCR_WIDTH, max(1, int(image.height * ratio))), Image.LANCZOS,
        )

    return ImageOps.autocontrast(image)


def _tesseract_text(image_bytes: bytes) -> str | None:
    """Synchronní volání tesseractu. Běží ve vlákně, viz `_extract_text`."""
    import pytesseract

    settings = get_settings()
    if settings.ocr_tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.ocr_tesseract_cmd

    # Česky i anglicky: účtenky mívají "Nafta"/"Natural" vedle "Total".
    # Když české jazykové dato chybí, tesseract by skončil chybou -
    # proto se na ni níž spadne zpátky na samotnou angličtinu.
    try:
        return pytesseract.image_to_string(_prepare(image_bytes), lang="ces+eng")
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(_prepare(image_bytes), lang="eng")


async def _extract_text(image_bytes: bytes) -> str | None:
    """Text z obrázku, nebo None.

    Tesseract je blokující a trvá stovky milisekund až sekundy, takže
    běží ve vlákně - jinak by zdržel celý event loop a s ním i ostatní
    požadavky."""
    provider = get_settings().ocr_provider
    if provider != PROVIDER_TESSERACT:
        logger.info("OCR poskytovatel %r není implementovaný", provider)
        return None
    return await asyncio.to_thread(_tesseract_text, image_bytes)
