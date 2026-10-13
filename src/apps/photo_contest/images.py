"""Photo-contest image checks and conversions.

Pure functions over an uploaded file object (``.size``, ``.seek``, ``.read``).
Size limits are module attributes read at call time, so tests can monkeypatch
them instead of building 15–20 MB files.
"""

import io
from contextlib import contextmanager
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

UPLOAD_MAX_BYTES = 20 * 1024 * 1024
STORE_MAX_BYTES = 15 * 1024 * 1024
# Above 48 Мп phones; one decoded RGB frame of this size is ~150 MB.
MAX_PIXELS = 50_000_000
PREVIEW_MAX_SIDE = 1600
THUMB_MAX_SIDE = 600
THUMB_QUALITY = 80
FALLBACK_MAX_SIDE = 6000
MIN_SIDE = 16
QUALITY_STEPS = (92, 88, 85, 80)
EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
# Pillow names a JPEG with extra frames "MPO": iPhone photos carry an HDR gain
# map that way. Browsers show its first frame, so it is a plain JPEG for us.
JPEG_ALIASES = {"MPO": "JPEG"}

TOO_BIG = "Файл больше 20 МБ."
BAD_FORMAT = "Поддерживаются JPEG, PNG и WebP."
TOO_MANY_PIXELS = "Слишком большое разрешение изображения."
UNREADABLE = "Не удалось прочитать изображение."

EXIF_ORIENTATION = 0x0112
# Orientations 5–8 rotate by 90°, so the displayed width/height swap.
SWAPPING_ORIENTATIONS = {5, 6, 7, 8}


class ImageRejected(ValueError):
    """An upload we can't accept; ``str(exc)`` is the message for the user."""


@dataclass(frozen=True)
class ImageInfo:
    format: str
    width: int
    height: int


@contextmanager
def _decoding():
    # probe() reads only the header, so a truncated or corrupt body surfaces
    # here, during the full decode.
    try:
        yield
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ImageRejected(UNREADABLE)


def probe(file):
    """Check size, format and resolution from the header only."""
    if file.size > UPLOAD_MAX_BYTES:
        raise ImageRejected(TOO_BIG)
    try:
        file.seek(0)
        with Image.open(file) as img:
            fmt = JPEG_ALIASES.get(img.format, img.format)
            width, height = img.size
            orientation = img.getexif().get(EXIF_ORIENTATION)
    except UnidentifiedImageError:
        # HEIC and other formats Pillow can't open land here.
        raise ImageRejected(BAD_FORMAT)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ImageRejected(UNREADABLE)
    finally:
        file.seek(0)
    if fmt not in EXTENSIONS:
        raise ImageRejected(BAD_FORMAT)
    if width * height > MAX_PIXELS:
        raise ImageRejected(TOO_MANY_PIXELS)
    if orientation in SWAPPING_ORIENTATIONS:
        width, height = height, width
    return ImageInfo(fmt, width, height)


def _to_rgb(img):
    if img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _jpeg_bytes(img, quality, exif=None):
    buffer = io.BytesIO()
    kwargs = {"exif": exif} if exif else {}
    img.save(buffer, format="JPEG", quality=quality, **kwargs)
    return buffer.getvalue()


def _fit_quality(img, exif):
    """JPEG bytes at the best quality step within ``STORE_MAX_BYTES``, or None."""
    for quality in QUALITY_STEPS:
        data = _jpeg_bytes(img, quality, exif)
        if len(data) <= STORE_MAX_BYTES:
            return data
    return None


def make_original(file, info):
    """Return ``(data, ext, width, height)`` of the original to store.

    A file within ``STORE_MAX_BYTES`` is kept byte for byte. A bigger one is
    re-encoded as JPEG at full resolution, lowering quality step by step and,
    as a last resort, downscaling until it fits. EXIF is carried over (originals are
    admin-only), with the orientation already applied to the pixels.
    """
    if file.size <= STORE_MAX_BYTES:
        file.seek(0)
        data = file.read()
        file.seek(0)
        return data, EXTENSIONS[info.format], info.width, info.height

    with _decoding():
        file.seek(0)
        with Image.open(file) as src:
            img = ImageOps.exif_transpose(src)
        exif = img.getexif()
        exif = exif.tobytes() if exif else None
        img = _to_rgb(img)
        data = _fit_quality(img, exif)
        if data is None:
            img.thumbnail((FALLBACK_MAX_SIDE, FALLBACK_MAX_SIDE))
            data = _fit_quality(img, exif)
        while data is None:
            # Very noisy pictures may still not fit: keep shrinking.
            side = int(max(img.size) * 0.8)
            if side < MIN_SIDE:
                raise ImageRejected(UNREADABLE)
            img.thumbnail((side, side))
            data = _fit_quality(img, exif)
        width, height = img.size
        img.close()
    file.seek(0)
    return data, "jpg", width, height


def make_preview(file, max_side=None, quality=85):
    """Return JPEG bytes of a downscaled, EXIF-free preview."""
    if max_side is None:
        max_side = PREVIEW_MAX_SIDE
    with _decoding():
        file.seek(0)
        with Image.open(file) as src:
            if JPEG_ALIASES.get(src.format, src.format) == "JPEG":
                # Decode straight at a reduced DCT scale: far less memory.
                src.draft("RGB", (max_side, max_side))
            img = ImageOps.exif_transpose(src)
        img = _to_rgb(img)
        img.thumbnail((max_side, max_side))
        data = _jpeg_bytes(img, quality)
        img.close()
    file.seek(0)
    return data
