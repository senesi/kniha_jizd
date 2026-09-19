"""Napojení tesseractu na čtení účtenek (zadání 15/25).

Parsování textu má vlastní testy jinde (`test_fuelings_etapa5.py`) - tady
jde o **napojení**: že engine funguje, když je po ruce, že jeho výpadek
nikdy nic neshodí, a že se stav tachometru záměrně nečte.
"""
import io
import shutil

import pytest
from PIL import Image, ImageDraw

from app.core import ocr


def _has_tesseract() -> bool:
    """Lokálně (Windows) obvykle není; v produkčním obrazu ano."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


needs_tesseract = pytest.mark.skipif(
    not _has_tesseract(), reason="tesseract není v PATH (běží jen v kontejneru)",
)


def _receipt_image(lines, *, width=700, height=420) -> bytes:
    """Nasimulovaná účtenka: tmavý text na světlém, rovné řádky.

    Není to fotka z ruky, takže tenhle test neříká nic o tom, jak dobře
    tesseract čte realitu - jen že celý řetězec od bajtů po návrh
    funguje."""
    image = Image.new("L", (width, height), 245)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((24, 24 + index * 42), line, fill=10)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- konfigurace -------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    """Výchozí stav je „nečíst nic" - aplikace nesmí na OCR záviset."""
    assert ocr.is_configured() is False
    assert ocr.reads_odometer() is False


async def test_nothing_is_read_when_not_configured():
    assert await ocr.read_receipt(b"cokoliv") is None
    assert await ocr.read_odometer(b"cokoliv") is None


async def test_odometer_stays_off_even_with_a_provider(monkeypatch):
    """Tesseract na displej za sklem nestačí a špatně přečtený stav km
    posouvá tachometr vozidla - je tedy horší než žádný."""
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)

    assert ocr.is_configured() is True
    assert ocr.reads_odometer() is False
    assert await ocr.read_odometer(_receipt_image(["123456 km"])) is None


async def test_odometer_can_be_switched_back_on(monkeypatch):
    """Kód pro tachometr zůstává funkční - čeká na lepší engine."""
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)
    monkeypatch.setattr(settings, "ocr_read_odometer", True, raising=False)

    assert ocr.reads_odometer() is True


async def test_unknown_provider_returns_nothing(monkeypatch):
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "nejaky-vymysleny", raising=False)
    assert await ocr.read_receipt(_receipt_image(["48,50 l"])) is None


# --- předzpracování ----------------------------------------------------

def test_small_image_is_enlarged():
    """Drobné písmo tesseractu výrazně vadí."""
    small = _receipt_image(["48,50 l"], width=300, height=200)
    prepared = ocr._prepare(small)

    assert prepared.width >= ocr.MIN_OCR_WIDTH
    assert prepared.mode == "L", "do šedi - barva jen mate"


def test_large_image_is_left_alone():
    big = _receipt_image(["48,50 l"], width=1600, height=900)
    assert ocr._prepare(big).width == 1600


async def test_broken_image_returns_none(monkeypatch):
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)
    assert await ocr.read_receipt(b"tohle rozhodne neni obrazek") is None


async def test_engine_failure_returns_none(monkeypatch):
    """Když tesseract spadne, tankování se uloží dál - jen bez návrhu."""
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)

    def boom(image_bytes):
        raise RuntimeError("tesseract není nainstalovaný")

    monkeypatch.setattr(ocr, "_tesseract_text", boom)
    assert await ocr.read_receipt(_receipt_image(["48,50 l"])) is None


# --- skutečné čtení (jen tam, kde tesseract je) ------------------------

@needs_tesseract
async def test_receipt_is_actually_read(monkeypatch):
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)

    reading = await ocr.read_receipt(_receipt_image([
        "CERPACI STANICE",
        "19.09.2026",
        "Nafta",
        "48,50 l",
        "38,90 Kc/l",
        "Celkem 1886,65 Kc",
    ]))

    assert reading is not None, "z čitelné účtenky se má něco přečíst"
    assert reading.has_anything


@needs_tesseract
async def test_unreadable_image_gives_nothing(monkeypatch):
    """Prázdný obrázek nesmí vyrobit vymyšlené hodnoty."""
    settings = ocr.get_settings()
    monkeypatch.setattr(settings, "ocr_provider", "tesseract", raising=False)

    blank = Image.new("L", (900, 600), 250)
    buffer = io.BytesIO()
    blank.save(buffer, format="PNG")

    assert await ocr.read_receipt(buffer.getvalue()) is None


# --- záměny písmen, které dělá skutečný tesseract ----------------------
#
# Tohle nejsou vymyšlené případy: na produkci tesseract přečetl
# "48,50 |" a "38,90 Kc/I" místo litrů. Text byl jinak správně, ale
# parser množství i cenu za litr zahodil.

@pytest.mark.parametrize("unit_char", ["l", "|", "I", "1"])
def test_liters_survive_ocr_lookalikes(unit_char):
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(f"Nafta\n48,50 {unit_char}\n")
    assert reading is not None
    assert reading.quantity == 48.5
    assert reading.unit == "l"


@pytest.mark.parametrize("unit_char", ["l", "|", "I"])
def test_price_per_liter_survives_ocr_lookalikes(unit_char):
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(f"38,90 Kc/{unit_char}\n")
    assert reading is not None
    assert reading.price_per_unit_czk == 38.9


def test_a_number_glued_to_one_is_not_litres():
    """„48,501" je číslo, ne 48,50 litru - tolerance nesmí zajít takhle
    daleko."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("48,501\n")
    assert reading is None or reading.quantity is None


def test_a_plain_price_is_not_litres():
    """V „1886,65 Kc" není jednotka, jen cena."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Celkem 1886,65 Kc\n")
    assert reading is not None
    assert reading.quantity is None
    assert reading.price_total_czk == 1886.65


def test_kwh_is_not_confused_with_litres():
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("42,00 kWh\n")
    assert reading.unit == "kWh"
    assert reading.quantity == 42.0


def test_real_receipt_text_from_production():
    """Doslova to, co tesseract přečetl z testovací účtenky na produkci."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(
        "CERPACI STANICE ONO\nMlada Boleslav\n19.09.2026 14:32\nNafta\n\n"
        "48,50 |\n\n38,90 Kc/I\n\nCelkem 1886,65 Kc\n"
    )
    assert reading is not None
    assert reading.quantity == 48.5
    assert reading.unit == "l"
    assert reading.price_per_unit_czk == 38.9
    assert reading.price_total_czk == 1886.65
    assert reading.fueled_at is not None
