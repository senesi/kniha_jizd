"""Miniatura náhledu dokumentu (zadání 18).

Seznam dokumentů byl dosud jen seznam názvů souborů. U vozidla, které má
technický průkaz, zelenou kartu, pojistku a k tomu tři faktury, je
„Otevřít" u každého řádku pomalá cesta k tomu jediný správný najít -
řidič dokument pozná od pohledu dřív, než přečte jeho název.

Proti `app/core/photos.py` tohle **nic neukládá při nahrání**. Náhled se
vyrábí až ve chvíli, kdy ho někdo chce, a teprve pak zůstane na disku
(viz `documents/service.py:load_preview`). Důvod je praktický: dokumenty
už na produkci jsou a takhle náhled dostanou i ony, bez migrace a bez
dávkového přepočtu.

Náhled je **pohodlí, ne funkce.** Když se vykreslit nepodaří - poškozený
soubor, PDF zašifrované heslem, HEIC, který Pillow bez `pillow-heif`
neotevře - vrátí se `None` a šablona nakreslí neutrální ikonu. Seznam
dokumentů musí fungovat dál, stejně jako u OCR a map (zadání 14/34).
"""
import io
from pathlib import Path

from PIL import Image, ImageOps

# pdfium je engine z Chrome a chodí jako samostatná wheel, ale tahle
# závislost nemá právo shodit start aplikace. Bez ní se PDF prostě
# nepředvádí a zůstane u ikony.
try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - závisí na prostředí, ne na kódu
    pdfium = None

# 400 px na delší straně: v mřížce se kreslí do 56-80 px, ale na retina
# displeji a po otevření náhledu na výšku musí pořád vypadat ostře.
PREVIEW_MAX_DIMENSION = 400
PREVIEW_JPEG_QUALITY = 78
PREVIEW_MIME = "image/jpeg"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
# .heic tu schválně není: Pillow ho bez pillow-heif neotevře, a nahrát
# ho jako dokument jde dál - jen bez miniatury.
PREVIEWABLE_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}


def is_previewable(suffix: str) -> bool:
    return suffix.lower() in PREVIEWABLE_EXTENSIONS and (
        suffix.lower() != ".pdf" or pdfium is not None
    )


def is_previewable_filename(filename: str) -> bool:
    """Totéž z celého jména souboru - tuhle variantu volají šablony, aby
    se nemusely pitvat v příponách."""
    return is_previewable(Path(filename).suffix)


def _to_jpeg(image: Image.Image) -> bytes:
    image.thumbnail((PREVIEW_MAX_DIMENSION, PREVIEW_MAX_DIMENSION))
    if image.mode != "RGB":
        # Sken s průhledností by se do JPEG uložil s černým pozadím -
        # bílé je to, co člověk u papíru čeká.
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            converted = image.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=PREVIEW_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def _image_preview(path: Path) -> bytes:
    with Image.open(path) as img:
        # Vyfocený technický průkaz má natočení v EXIF; bez tohohle by
        # ležel na boku právě v tom malém okně, kde se hůř pozná.
        return _to_jpeg(ImageOps.exif_transpose(img))


def _pdf_preview(path: Path) -> bytes:
    document = pdfium.PdfDocument(path)
    try:
        page = document[0]
        # Měřítko se dopočítá z rozměru stránky (v bodech, 72 dpi), aby
        # se A4 i účtenka z benzínky renderovaly na podobně velký
        # obrázek. Strop je kvůli tomu, aby vizitka nevyrobila
        # několikamegapixelový bitmap.
        longest_side = max(page.get_width(), page.get_height()) or 1
        scale = min(4.0, max(0.25, PREVIEW_MAX_DIMENSION / longest_side))
        bitmap = page.render(scale=scale)
        try:
            return _to_jpeg(bitmap.to_pil())
        finally:
            bitmap.close()
    finally:
        document.close()


def render_preview(path: Path) -> bytes | None:
    """JPEG miniatura prvního listu dokumentu, nebo `None`.

    `None` znamená „náhled nedává smysl nebo se nepovedl" - volající to
    nemá řešit jako chybu."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _pdf_preview(path) if pdfium is not None else None
        if suffix in IMAGE_EXTENSIONS:
            return _image_preview(path)
    except Exception:
        # Záměrně široké: pdfium i Pillow umí na poškozeném souboru
        # vyhodit prakticky cokoliv a zajímá nás jediné - náhled nebude.
        return None
    return None
