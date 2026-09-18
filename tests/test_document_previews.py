"""Miniatury náhledů dokumentů (zadání 18).

Náhled je pohodlí, ne funkce - proto se tady netestuje jen to, že se
vyrobí, ale hlavně že když se vyrobit nedá, nic nespadne a že se k němu
nedostane nikdo, kdo nevidí samotné vozidlo.
"""
import io

import pytest
from PIL import Image

from app.core import previews


def _png_bytes(size=(1200, 900), color=(20, 120, 140)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def _minimal_pdf() -> bytes:
    """Nejmenší platné PDF, jaké pdfium otevře - jedna prázdná A4."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at,
    )
    return bytes(out)


# --- co se vůbec předvádí ---------------------------------------------

def test_previewable_types():
    assert previews.is_previewable_filename("a1b2.jpg")
    assert previews.is_previewable_filename("a1b2.PNG")      # přípona bez ohledu na velikost písmen
    assert previews.is_previewable_filename("a1b2.webp")
    # HEIC Pillow bez pillow-heif neotevře - nahrát ho jde, náhled nebude.
    assert not previews.is_previewable_filename("a1b2.heic")
    assert not previews.is_previewable_filename("a1b2.docx")
    assert not previews.is_previewable_filename("bez_pripony")


def test_pdf_is_previewable_when_renderer_is_installed():
    assert previews.is_previewable_filename("a1b2.pdf") is (previews.pdfium is not None)


# --- vykreslení --------------------------------------------------------

def test_image_preview_is_jpeg_within_the_size_limit(tmp_path):
    source = tmp_path / "sken.png"
    source.write_bytes(_png_bytes())

    data = previews.render_preview(source)
    assert data is not None

    with Image.open(io.BytesIO(data)) as thumb:
        assert thumb.format == "JPEG"
        assert max(thumb.size) <= previews.PREVIEW_MAX_DIMENSION
        # Poměr stran zůstává - zdroj 1200x900 je 4:3.
        assert thumb.size[0] > thumb.size[1]


def test_transparent_image_gets_white_background(tmp_path):
    source = tmp_path / "razitko.png"
    out = io.BytesIO()
    Image.new("RGBA", (200, 200), (0, 0, 0, 0)).save(out, format="PNG")
    source.write_bytes(out.getvalue())

    data = previews.render_preview(source)
    assert data is not None
    with Image.open(io.BytesIO(data)) as thumb:
        # Průhledné pozadí by v JPEG zčernalo; u papíru čekáme bílou.
        assert thumb.convert("RGB").getpixel((10, 10)) == (255, 255, 255)


@pytest.mark.skipif(previews.pdfium is None, reason="pypdfium2 není nainstalované")
def test_pdf_first_page_renders(tmp_path):
    source = tmp_path / "faktura.pdf"
    source.write_bytes(_minimal_pdf())

    data = previews.render_preview(source)
    assert data is not None
    with Image.open(io.BytesIO(data)) as thumb:
        assert thumb.format == "JPEG"
        assert max(thumb.size) <= previews.PREVIEW_MAX_DIMENSION
        # A4 je na výšku, náhled to musí zachovat.
        assert thumb.size[1] > thumb.size[0]


# --- co se pokazit smí -------------------------------------------------

def test_broken_file_returns_none_instead_of_raising(tmp_path):
    """Poškozený soubor nesmí shodit seznam dokumentů."""
    source = tmp_path / "rozbite.pdf"
    source.write_bytes(b"%PDF-1.4\nrozhodne to neni pdf\n")
    assert previews.render_preview(source) is None

    source = tmp_path / "rozbite.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    assert previews.render_preview(source) is None


def test_unsupported_and_missing_files_return_none(tmp_path):
    assert previews.render_preview(tmp_path / "neexistuje.jpg") is None

    source = tmp_path / "tabulka.xlsx"
    source.write_bytes(b"PK\x03\x04")
    assert previews.render_preview(source) is None
