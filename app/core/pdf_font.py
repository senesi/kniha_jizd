"""Font pro PDF export s českou diakritikou (zadání 23).

Proč to má vlastní modul: reportlab umí ze standardních fontů jen
WinAnsi, a Bitstream Vera, kterou přibaluje, nemá **ě ř ů ť ď ň**. PDF
kniha jízd by tedy byla plná prázdných míst zrovna ve slovech jako
„Přehled", „Řidič" nebo „Účel" - a to je v české aplikaci nepoužitelné.

Řešení je postupné, od nejlepšího k nejhoršímu:

1. `PDF_FONT_PATH` z konfigurace - pro lokální vývoj na Windows, kde se
   dá ukázat na systémový font. Do repozitáře se žádný font nekopíruje:
   systémové fonty se nesmí šířit a 750 kB binárky v Gitu není řešení.
2. DejaVu Sans z obrazu (`fonts-dejavu-core`, viz docker/Dockerfile).
   Tohle je cesta, kterou jede produkce.
3. Vera + složení diakritiky na holé písmeno. Ošklivé, ale PDF vznikne a
   dá se přečíst - export nesmí spadnout jen proto, že chybí font.

Zjišťuje se to jednou a výsledek se drží, aby se font neregistroval při
každém stažení.
"""
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.core.config import get_settings

# Písmena, kterými se poznává, jestli font na češtinu stačí. Zbytek
# (á í é ú ý č š ž) umí i Vera, takže by test neprošel jen na nich.
CZECH_PROBE = "ěřůťďňĚŘŮŤĎŇ"

# Kde DejaVu leží v Debianu po `apt-get install fonts-dejavu-core`.
LINUX_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

_VERA_DIR = Path(reportlab.__file__).parent / "fonts"


@dataclass(frozen=True)
class PdfFont:
    regular: str
    bold: str
    #: True, když font češtinu neumí a text se musí složit na holá písmena.
    needs_folding: bool


def _covers_czech(path: Path) -> bool:
    try:
        face = TTFont("probe", str(path)).face
    except Exception:
        return False
    return all(face.charToGlyph.get(ord(char)) for char in CZECH_PROBE)


def _bold_sibling(path: Path) -> Path | None:
    """DejaVuSans.ttf -> DejaVuSans-Bold.ttf, Vera.ttf -> VeraBd.ttf."""
    for candidate in (
        path.with_name(f"{path.stem}-Bold{path.suffix}"),
        path.with_name(f"{path.stem}Bd{path.suffix}"),
        path.with_name(f"{path.stem}bd{path.suffix}"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _register(path: Path) -> PdfFont:
    regular_name = f"kj-{path.stem}"
    pdfmetrics.registerFont(TTFont(regular_name, str(path)))

    bold_path = _bold_sibling(path)
    if bold_path is not None:
        bold_name = f"kj-{bold_path.stem}"
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    else:
        # Bez řezu tučného se nadpisy vysází základním - lepší než pád.
        bold_name = regular_name

    return PdfFont(regular=regular_name, bold=bold_name, needs_folding=not _covers_czech(path))


@lru_cache(maxsize=1)
def get_pdf_font() -> PdfFont:
    configured = (get_settings().pdf_font_path or "").strip()
    candidates = ([Path(configured)] if configured else []) + [
        Path(p) for p in LINUX_FONT_CANDIDATES
    ]
    for path in candidates:
        if path.is_file() and _covers_czech(path):
            return _register(path)

    # Poslední záchrana - font, který je vždycky po ruce.
    return _register(_VERA_DIR / "Vera.ttf")


def fold(text: str, font: PdfFont) -> str:
    """Sundá z písmen diakritiku, když ji font neumí vykreslit.

    Používá se jen ve třetím scénáři (viz modul docstring). Když font
    češtinu umí, text se nemění - a to je stav, ve kterém běží produkce."""
    if not font.needs_folding or not text:
        return text
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize(
        "NFC", "".join(char for char in decomposed if not unicodedata.combining(char)),
    )
