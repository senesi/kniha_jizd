"""Single choke point for every photo upload in the app.

Odometer shots and fuel receipts have to stay readable afterwards - that is
the whole point of photographing them (zadání 8/11/24) - so the "full"
variant is kept at a higher resolution and quality than a gallery picture
would need, while still being resized down from a modern phone's 12 Mpx
original.
"""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

# Not the browser-supplied content_type (unreliable - some browsers/OSes
# report a generic "application/octet-stream" for a perfectly valid image,
# which would wrongly reject a real upload). The extension plus actually
# decoding the file with Pillow is what is trusted.
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_PHOTO_UPLOAD_BYTES = 12 * 1024 * 1024  # 12 MB - a phone photo, resized down immediately after anyway
THUMBNAIL_MAX_DIMENSION = 800
# 2400 px (not 1800) with quality 90: an odometer or a fuel receipt has to
# stay legible enough to check a digit against what the driver typed.
FULL_MAX_DIMENSION = 2400
FULL_JPEG_QUALITY = 90
_PIL_FORMAT_BY_EXT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".gif": "GIF"}


class UnsupportedPhotoType(Exception):
    pass


class PhotoTooLarge(Exception):
    pass


def _resize_variant(data: bytes, ext: str, max_dimension: int, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        # Phone cameras store rotation in EXIF; without this a landscape
        # odometer shot renders sideways and is much harder to read.
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_dimension, max_dimension))

        out = io.BytesIO()
        save_format = _PIL_FORMAT_BY_EXT.get(ext, img.format or "JPEG")
        if save_format == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        save_kwargs = {"quality": quality, "optimize": True} if save_format == "JPEG" else {}
        img.save(out, format=save_format, **save_kwargs)
        return out.getvalue()


def process_upload(original_filename: str, data: bytes) -> tuple[str, bytes, bytes]:
    """Validate and resize an uploaded photo. Returns (ext, thumbnail_bytes,
    full_bytes); raises PhotoTooLarge / UnsupportedPhotoType on rejection."""
    if len(data) > MAX_PHOTO_UPLOAD_BYTES:
        raise PhotoTooLarge(f"Fotografie přesahuje maximální velikost ({MAX_PHOTO_UPLOAD_BYTES // (1024 * 1024)} MB)")

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise UnsupportedPhotoType(f"Nepodporovaný typ fotografie: {ext or '(bez přípony)'}")

    try:
        thumbnail_bytes = _resize_variant(data, ext, THUMBNAIL_MAX_DIMENSION, 85)
        full_bytes = _resize_variant(data, ext, FULL_MAX_DIMENSION, FULL_JPEG_QUALITY)
    except Exception as exc:  # e.g. PIL.UnidentifiedImageError - not a real image despite the extension
        raise UnsupportedPhotoType(f"Soubor se nepodařilo zpracovat jako obrázek: {exc}") from exc

    return ext, thumbnail_bytes, full_bytes


def save_photo_files(photos_dir: Path, ext: str, thumbnail_bytes: bytes, full_bytes: bytes) -> tuple[str, str]:
    """Writes both resized variants to disk under random UUID names (never
    the user-supplied filename - no path traversal, no collisions, and two
    uploads can never overwrite each other) and returns their filenames."""
    photos_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    thumbnail_filename = f"{stem}_thumb{ext}"
    full_filename = f"{stem}_full{ext}"
    (photos_dir / thumbnail_filename).write_bytes(thumbnail_bytes)
    (photos_dir / full_filename).write_bytes(full_bytes)
    return thumbnail_filename, full_filename
