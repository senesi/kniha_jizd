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


# ======================================================================
# Spuštění OCR z formuláře
# ======================================================================
#
# Původní návrh měl slepou uličku: OCR se spouštělo až při odeslání
# formuláře, jenže pole „Množství" má required, takže prohlížeč odeslání
# zablokoval. Uživatel musel vyplnit přesně to, co mu mělo OCR nabídnout.

from datetime import date  # noqa: E402

from tests.conftest import create_vehicle, extract_csrf_token  # noqa: E402


def _png(text: str = "48,50 l") -> bytes:
    return _receipt_image([text])


async def test_form_offers_a_button_to_read_the_receipt(logged_in_client, csrf_token, monkeypatch):
    """Bez něj se k OCR nedá dostat - required na množství to zablokuje."""
    monkeypatch.setattr(ocr.get_settings(), "ocr_provider", "tesseract", raising=False)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-01", license_plate="1OC 0001",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")

    assert 'value="read_receipt"' in form.text
    # formnovalidate je tu to podstatné - jinak prohlížeč odeslání zastaví.
    assert "formnovalidate" in form.text


async def test_button_is_hidden_without_ocr(logged_in_client, csrf_token):
    """Tlačítko, které nic nepřečte, jen mate."""
    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-02", license_plate="1OC 0002",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")
    assert 'value="read_receipt"' not in form.text


async def test_reading_works_without_filling_quantity(logged_in_client, csrf_token, monkeypatch):
    """Jádro opravy: odeslání BEZ množství musí projít a spustit OCR."""
    monkeypatch.setattr(ocr.get_settings(), "ocr_provider", "tesseract", raising=False)

    async def fake_read(image_bytes, known_stations=()):
        from app.core.ocr import ReceiptReading
        return ReceiptReading(quantity=48.5, unit="l", price_total_czk=1886.65)

    monkeypatch.setattr(ocr, "read_receipt", fake_read)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-03", license_plate="1OC 0003",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(),
              "action": "read_receipt", "quantity": "", "odometer_km": ""},
        files={"receipt": ("uctenka.png", _png(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Z účtenky jsme přečetli" in response.text
    assert "48,5" in response.text or "48.5" in response.text


async def test_reading_without_a_photo_says_so(logged_in_client, csrf_token, monkeypatch):
    monkeypatch.setattr(ocr.get_settings(), "ocr_provider", "tesseract", raising=False)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-04", license_plate="1OC 0004",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(), "action": "read_receipt", "quantity": ""},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Nejdřív přiložte fotografii" in response.text


async def test_unreadable_receipt_is_still_kept(logged_in_client, csrf_token, monkeypatch):
    """Účtenku už uživatel jednou vyfotil - nutit ho to opakovat jen
    proto, že z ní nic nevyšlo, by bylo horší než ji nepřečíst."""
    monkeypatch.setattr(ocr.get_settings(), "ocr_provider", "tesseract", raising=False)

    async def reads_nothing(image_bytes, known_stations=()):
        return None

    monkeypatch.setattr(ocr, "read_receipt", reads_nothing)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-05", license_plate="1OC 0005",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(), "action": "read_receipt", "quantity": ""},
        files={"receipt": ("uctenka.png", _png("neni tu nic"), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "nepodařilo nic přečíst" in response.text
    # Uložená je - formulář si ji drží pro následné uložení.
    assert 'name="receipt_attachment_id"' in response.text


async def test_normal_save_still_works_when_ocr_reads_nothing(
    logged_in_client, csrf_token, monkeypatch,
):
    """Regrese: běžné uložení s fotkou nesmí opravou zmizet."""
    monkeypatch.setattr(ocr.get_settings(), "ocr_provider", "tesseract", raising=False)

    async def reads_nothing(image_bytes, known_stations=()):
        return None

    monkeypatch.setattr(ocr, "read_receipt", reads_nothing)

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="OC-06", license_plate="1OC 0006",
        current_odometer_km="100000",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")

    response = await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(),
              "quantity": "42", "odometer_km": "100300"},
        files={"receipt": ("uctenka.png", _png(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


# ======================================================================
# Tvar skutečné české účtenky
# ======================================================================
#
# Parser byl postavený na tvaru "48,50 l", který jsem si vymyslel v
# testovacím obrázku. Skutečná účtenka z pumpy píše popisek PŘED číslo:
# "Litry : 0047.45". Z první reálné účtenky se proto přečetlo jen datum
# a celková částka - zrovna množství a cena za litr chyběly.

REAL_RECEIPT = """=TPA cz S.n.d.
Ja
na Masaryka 708712
Praha 2, 120 00
Stojan: 1 Nafta
Řidič : OOOOO - a
Stroj : 10013 -— D
SP : Skamas tavOq
Litry : 0047.45
Ke “1 38,50
Celkem: 01826,80 Kč
22.10.2023
18:18
01238
Datum *
Cas :
Doklad č-"""


def test_real_czech_pump_receipt():
    """Doslova to, co tesseract přečetl z první skutečné účtenky."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(REAL_RECEIPT)

    assert reading is not None
    assert reading.quantity == 47.45
    assert reading.unit == "l"
    assert reading.price_total_czk == 1826.80
    assert str(reading.fueled_at) == "2023-10-22"
    # Cena za litr se z "Ke “1 38,50" vyčíst nedá, ale dopočítá se.
    assert reading.price_per_unit_czk == 38.50


def test_receipt_numbers_are_not_mistaken_for_quantity():
    """Na účtence je i číslo stojanu, stroje a dokladu."""
    from app.core.ocr import parse_receipt_text

    for noise in ("Stojan: 1 Nafta", "Doklad c: 01238", "Stroj : 10013"):
        reading = parse_receipt_text(noise)
        assert reading is None or reading.quantity is None, noise


@pytest.mark.parametrize("line,expected", [
    ("Litry : 0047.45", 47.45),
    ("Litry: 47,45", 47.45),
    ("Litru 47.45", 47.45),
    ("Objem: 42,10", 42.10),
    ("Množství 31,5", 31.5),
    ("Mnozstvi 31,5", 31.5),
])
def test_labelled_quantity_forms(line, expected):
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(line)
    assert reading is not None, line
    assert reading.quantity == expected
    assert reading.unit == "l"


@pytest.mark.parametrize("line", ["kWh : 38,20", "Energie: 38,20", "38,20 kWh"])
def test_labelled_kwh_forms(line):
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(line)
    assert reading is not None, line
    assert reading.unit == "kWh"
    assert reading.quantity == 38.20


def test_price_per_unit_is_derived_when_unreadable():
    """Spolehlivější než hádat z rozsypaného „Kč/l"."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Litry : 40,00\nCelkem: 1400,00 Kč")
    assert reading.price_per_unit_czk == 35.00


def test_printed_price_per_unit_wins_over_the_derived_one():
    """Když je čitelná, bere se ta z účtenky."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("38,90 Kc/l\nLitry : 40,00\nCelkem: 1400,00 Kč")
    assert reading.price_per_unit_czk == 38.90


def test_nothing_is_derived_without_both_numbers():
    from app.core.ocr import parse_receipt_text

    only_quantity = parse_receipt_text("Litry : 40,00")
    assert only_quantity.price_per_unit_czk is None


# ======================================================================
# Chyby nalezené na dalších skutečných účtenkách
# ======================================================================

def test_space_after_the_decimal_comma():
    """„0057, 46" je 57,46, ne 57.

    Nejhorší druh chyby, jakou OCR může udělat: výsledek vypadá
    věrohodně a uživatel ho potvrdí. Přečetlo se to na skutečné
    účtence."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Litry : 0057, 46\nCelkem: 01752,50 Kč")
    assert reading.quantity == 57.46
    # A hned se tím spraví i dopočet ceny za litr.
    assert reading.price_per_unit_czk == 30.50


@pytest.mark.parametrize("line", [
    "22,10,2023",       # čárky místo teček
    "22.10,2023",       # každý oddělovač jiný
    "Datum : 22.10.2023",
    "22. 10. 2023",
])
def test_date_separators_may_be_commas(line):
    """Tečka a čárka jsou na tisku k nerozeznání."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(f"Litry : 40,00\n{line}")
    assert str(reading.fueled_at) == "2023-10-22", line


def test_total_survives_a_lost_first_letter():
    """Tesseract z „Celkem:" udělal „elkem:"."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Litry : 47,45\nelkem: O01826,80 Kč")
    assert reading.price_total_czk == 1826.80


def test_newest_real_receipt():
    """Doslova to, co tesseract přečetl z nejnovější účtenky."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(
        "sTPA CZ s.r.0.\nJana Masaryka 708/12\nPraha Z, 6 oo\nYad\n"
        "Stojan: 1 Nafta\nŘidič : 00000 - O\nStroj : 10013 - D\n"
        "SP2 : SkamastavO4\nLitry : 0057, 46\nKé “1 : 30,50\n"
        "Celkem: 01752,50 Kč\nDatum : 17.02.2026\n"
    )
    assert reading.quantity == 57.46
    assert reading.unit == "l"
    assert reading.price_total_czk == 1752.50
    assert reading.price_per_unit_czk == 30.50
    assert str(reading.fueled_at) == "2026-02-17"
    assert reading.station is not None and "TPA CZ" in reading.station


# --- název stanice -----------------------------------------------------

@pytest.mark.parametrize("chain", ["Benzina", "ORLEN", "Shell", "MOL", "OMV", "EuroOil"])
def test_known_chains_are_recognised(chain):
    """Ze známé sítě vyjde čisté jméno, ne to, co z loga zbylo po OCR."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(f"{chain} a.s.\nPraha\nLitry : 40,00")
    assert reading.station == chain


def test_header_is_used_when_no_chain_matches():
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("TPA CZ s.r.o.\nJana Masaryka 708/12\nLitry : 40,00")
    assert reading.station == "TPA CZ s.r.o"


@pytest.mark.parametrize("junk", ["=TPA CZ s.r.o.", "|TPA CZ s.r.o.", "  TPA CZ s.r.o."])
def test_leading_non_letter_junk_is_stripped(junk):
    """Z okraje účtenky OCR často udělá nepísmenný znak před názvem.

    Pozor: **písmeno se nemaže.** Dřív tu bylo i „sTPA CZ", jenže to „S"
    do názvu patřilo - viz test_leading_letter_belongs_to_the_name."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(f"{junk}\nLitry : 40,00")
    assert reading.station.startswith("TPA CZ")


def test_no_station_rather_than_a_wrong_one():
    """Když se hlavička nepřečetla, první řádek je údaj o tankování -
    ten se jako název firmy nabídnout nesmí."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Stojan: 1 Nafta\nLitry : 40,00\nCelkem: 1400,00 Kč")
    assert reading.station is None


def test_address_is_not_a_station_name():
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("708/12 Jana Masaryka\nLitry : 40,00")
    assert reading.station is None


def test_station_reaches_the_suggestion_box(monkeypatch):
    """Pole pro stanici ve formuláři existovalo od začátku, jen ho OCR
    nikdy nevyplnilo."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("Benzina\nLitry : 40,00")
    assert reading.station == "Benzina"
    assert reading.has_anything


def test_footer_sentence_is_not_a_station():
    """„dekujeme za nakup" je patička, ne název firmy."""
    from app.core.ocr import parse_receipt_text

    assert parse_receipt_text("dekujeme za nakup") is None

    with_data = parse_receipt_text("dekujeme za nakup\nLitry : 40,00")
    assert with_data.station is None
    assert with_data.quantity == 40.0


def test_station_alone_is_not_worth_a_suggestion():
    """Odhadnutý název bez jediného čísla není nabídka."""
    from app.core.ocr import parse_receipt_text

    assert parse_receipt_text("TPA CZ s.r.o.\nJana Masaryka 708/12") is None


@pytest.mark.parametrize("line", [
    "Sta,",                 # útržek nepřečteného řádku
    "»PA : SkamastavoO4",   # popisek : hodnota
    "Stojan: 1 Nafta",
    "AB",                   # příliš krátké
])
def test_header_fragments_are_not_stations(line):
    """Obojí prošlo na skutečné účtence, než se pravidla přitvrdila."""
    from app.core.ocr import _clean_station_line

    assert _clean_station_line(line) is None


@pytest.mark.parametrize("line,expected", [
    ("TPA CZ s.r.o.", "TPA CZ s.r.o"),
    ("CERPACI STANICE", "CERPACI STANICE"),
    ("MOL Ceska republika", "MOL Ceska republika"),
])
def test_real_headers_survive_the_stricter_rules(line, expected):
    from app.core.ocr import _clean_station_line

    assert _clean_station_line(line) == expected


# ======================================================================
# Název stanice se učí z toho, co už uživatel zadal
# ======================================================================

def test_leading_letter_belongs_to_the_name():
    """Původně se malé písmeno před velkými maže jako smetí z okraje.

    Jenže u „STPA CZ s. r. o." to „S" do názvu patří - OCR jen přečetlo
    velké písmeno jako malé. Heuristika tím ničila správnou informaci."""
    from app.core.ocr import _clean_station_line

    assert _clean_station_line("sTPA CZ s.r.0.") == "STPA CZ s.r.0"


@pytest.mark.parametrize("mangled", [
    "sTPA CZ s.r.0.",
    "=TPA cz S.n.d.",
    "STPA CZ s.¥Y.0,",
    "STPA CZ s.r.o.",
])
def test_known_station_wins_over_ocr_noise(mangled):
    """Jakmile uživatel název jednou opraví, pozná se i rozsypaný."""
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text(
        f"{mangled}\nLitry : 40,00", known_stations=["STPA CZ s. r. o."],
    )
    assert reading.station == "STPA CZ s. r. o."


def test_a_different_station_is_not_absorbed():
    """Uložené jméno nesmí přebít stanici, která s ním nemá nic
    společného."""
    from app.core.ocr import parse_receipt_text

    for other in ("Benzina a.s.", "OMV Ceska republika", "Globus Praha"):
        reading = parse_receipt_text(
            f"{other}\nLitry : 40,00", known_stations=["STPA CZ s. r. o."],
        )
        assert reading.station != "STPA CZ s. r. o.", other


def test_without_a_dictionary_the_header_is_used_as_read():
    from app.core.ocr import parse_receipt_text

    reading = parse_receipt_text("STPA CZ s.r.o.\nLitry : 40,00")
    assert reading.station == "STPA CZ s.r.o"


def test_station_key_ignores_punctuation_and_case():
    """„s. r. o." a „s.r.0." se musí dostat na dostřel."""
    from app.core.ocr import _station_key

    assert _station_key("STPA CZ s. r. o.") == "stpaczsro"
    assert _station_key("sTPA CZ s.r.o") == "stpaczsro"


async def test_known_stations_come_from_previous_fuelings(logged_in_client, csrf_token):
    """Slovník se plní sám tím, jak lidé tankování zapisují."""
    from app.core.db import async_session_factory
    from app.modules.fuelings import repository
    from tests.conftest import create_vehicle

    vehicle_id = await create_vehicle(
        logged_in_client, csrf_token, internal_code="ST-01", license_plate="1ST 0001",
        current_odometer_km="100000",
    )
    form = await logged_in_client.get(f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new")
    await logged_in_client.post(
        f"/kniha-jizd/vehicles/{vehicle_id}/fuelings/new",
        data={"csrf_token": extract_csrf_token(form.text),
              "fueled_at": date.today().isoformat(), "quantity": "40",
              "odometer_km": "100200", "station": "STPA CZ s. r. o."},
        follow_redirects=False,
    )

    async with async_session_factory() as db:
        stations = await repository.known_stations(db)

    assert "STPA CZ s. r. o." in stations
