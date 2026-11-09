"""Cover and logo uploads of the race form.

Pure functions over an uploaded file object. Crop coordinates are pixels of the
image as the browser shows it, so EXIF rotation is applied before cropping.
"""

import io
import logging
import re
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import DatabaseError
from django.db.models import Q
from PIL import Image, ImageOps

from apps.photo_contest.images import UNREADABLE, ImageRejected, probe
from website.models.race import Race

logger = logging.getLogger(__name__)

UPLOAD_DIR = "race_headers"
# Field values are free text typed by a race admin, so only an exact generated
# name may ever reach storage.delete(): a prefix test would accept "../other".
STORED_NAME_RE = re.compile(rf"{UPLOAD_DIR}/[0-9a-f]{{32}}\.(jpg|png)")

COVER_RATIO = 4
COVER_MAX_WIDTH = 1920
COVER_QUALITY = 85
LOGO_MAX_SIDE = 512
MIN_SIDE = 16

BAD_CROP = "Некорректная область кадрирования."
TOO_SMALL = "Изображение или выбранная область слишком маленькие."
SAVE_FAILED = "Не удалось сохранить файл. Попробуйте ещё раз."


def parse_crop(raw):
    """``(x, y, width, height)`` from ``"x,y,width,height"``, None when empty."""
    raw = (raw or "").strip()
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise ImageRejected(BAD_CROP)
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError:
        raise ImageRejected(BAD_CROP)
    if width <= 0 or height <= 0:
        raise ImageRejected(BAD_CROP)
    return x, y, width, height


def _load(file):
    probe(file)
    # probe() reads only the header; a corrupt body surfaces on the full decode.
    try:
        file.seek(0)
        with Image.open(file) as src:
            return ImageOps.exif_transpose(src)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ImageRejected(UNREADABLE)


def _box(crop, size):
    x, y, width, height = crop
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + width, size[0]), min(y + height, size[1])
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        raise ImageRejected(TOO_SMALL)
    return left, top, right, bottom


def _centre_cover_crop(size):
    width, height = size
    if width >= height * COVER_RATIO:
        crop_width = height * COVER_RATIO
        return (width - crop_width) // 2, 0, crop_width, height
    crop_height = width // COVER_RATIO
    return 0, (height - crop_height) // 2, width, crop_height


def _flatten(img):
    if img.mode == "RGB":
        return img
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def process_cover(file, crop):
    """Return ``(bytes, ext)`` of the cover; no crop means a centre-cut 4:1."""
    img = _load(file)
    if crop is None:
        crop = _centre_cover_crop(img.size)
    img = _flatten(img.crop(_box(crop, img.size)))
    if img.width > COVER_MAX_WIDTH:
        img.thumbnail((COVER_MAX_WIDTH, img.height))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=COVER_QUALITY)
    return buffer.getvalue(), "jpg"


def process_logo(file, crop):
    """Return ``(bytes, ext)`` of the logo; no crop means the whole image."""
    img = _load(file)
    # A CMYK image can't be written as PNG, and a palette one would downscale
    # with nearest-neighbour edges.
    img = img.convert("RGBA")
    if crop is None:
        crop = (0, 0, img.width, img.height)
    img = img.crop(_box(crop, img.size))
    img.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue(), "png"


def _remove(name):
    try:
        default_storage.delete(name)
    except OSError:
        logger.exception("Could not delete race header file %s", name)


def _stored_name(value):
    """The storage name of a value this module generated, otherwise None."""
    if not value.startswith(settings.MEDIA_URL):
        return None
    name = value[len(settings.MEDIA_URL) :]
    return name if STORED_NAME_RE.fullmatch(name) else None


def store_upload(data, ext):
    """Write the image and return the value to keep in the race field."""
    name = f"{UPLOAD_DIR}/{uuid.uuid4().hex}.{ext}"
    try:
        default_storage.save(name, ContentFile(data))
    except OSError:
        # A full disk can leave a truncated file behind.
        _remove(name)
        raise ImageRejected(SAVE_FAILED)
    return default_storage.url(name)


def discard_stored(values):
    """Remove files written by a save that did not go through."""
    for value in values:
        name = _stored_name(value)
        if name:
            _remove(name)


def delete_if_unused(value):
    """Delete a replaced file. Never raises: the race is already saved."""
    name = _stored_name(value)
    if not name:
        return
    try:
        if Race.objects.filter(Q(header_image=value) | Q(header_logo=value)).exists():
            return
    except DatabaseError:
        logger.exception("Could not check usage of race header file %s", name)
        return
    _remove(name)
