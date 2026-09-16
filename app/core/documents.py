"""Vehicle document uploads (TP, OTP, zelená karta, ...) - zadání 18.

Separate from app/core/photos.py because a document is stored as-is: a PDF
cannot go through the image pipeline, and even an image document (a phone
photo of a TP) must not be re-encoded down to a size where small print
stops being readable.

Both this and photos.py write random UUID filenames into a directory that
is never served statically - every read goes through an authorizing route
(see app/modules/documents/router_web.py), so no document is reachable
from a guessable URL (zadání 18/30).
"""
import uuid
from pathlib import Path

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_DOCUMENT_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB - a scanned multi-page PDF is bigger than a photo

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}

# First bytes that must be present for the extension to be believed. The
# browser-supplied content type is never trusted; a file claiming ".pdf"
# that does not start with %PDF- is rejected outright.
_MAGIC_BY_EXT = {
    ".pdf": [b"%PDF-"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".webp": [b"RIFF"],
    ".heic": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00 ftyp"],
}


class UnsupportedDocumentType(Exception):
    pass


class DocumentTooLarge(Exception):
    pass


def validate_document(original_filename: str, data: bytes) -> tuple[str, str]:
    """Returns (ext, mime_type); raises on rejection."""
    if len(data) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise DocumentTooLarge(
            f"Soubor přesahuje maximální velikost ({MAX_DOCUMENT_UPLOAD_BYTES // (1024 * 1024)} MB)"
        )
    if not data:
        raise UnsupportedDocumentType("Soubor je prázdný.")

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise UnsupportedDocumentType(f"Nepodporovaný typ souboru: {ext or '(bez přípony)'}")

    magics = _MAGIC_BY_EXT.get(ext, [])
    if magics and not any(data.startswith(m) for m in magics):
        raise UnsupportedDocumentType("Obsah souboru neodpovídá jeho příponě.")

    return ext, _MIME_BY_EXT[ext]


def save_document_file(documents_dir: Path, ext: str, data: bytes) -> str:
    documents_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{ext}"
    (documents_dir / stored_filename).write_bytes(data)
    return stored_filename
