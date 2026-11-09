import io
import re

import pytest
from django.contrib.auth.models import User
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.photo_contest.images import ImageRejected
from apps.race.forms import RaceForm
from apps.race.header_images import (
    SAVE_FAILED,
    delete_if_unused,
    parse_crop,
    process_cover,
    process_logo,
    store_upload,
)
from website.models.race import Race, RaceAdmin, RegStatus

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)


def upload(img, fmt="PNG", name="x.png", **save_kwargs):
    buffer = io.BytesIO()
    img.save(buffer, format=fmt, **save_kwargs)
    return SimpleUploadedFile(name, buffer.getvalue())


def opened(data):
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def close_to(pixel, colour, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], colour))


def split_red_blue(width, height):
    """Left half red, right half blue."""
    img = Image.new("RGB", (width, height), RED)
    img.paste(BLUE, (width // 2, 0, width, height))
    return img


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        ("  ", None),
        (None, None),
        ("1,2,30,40", (1, 2, 30, 40)),
        (" -5, 0, 30, 40 ", (-5, 0, 30, 40)),
    ],
)
def test_parse_crop_valid(raw, expected):
    assert parse_crop(raw) == expected


@pytest.mark.parametrize(
    "raw", ["1,2,3", "1,2,3,4,5", "a,b,c,d", "1.5,2,3,4", "0,0,0,10", "0,0,10,-1"]
)
def test_parse_crop_rejects_malformed(raw):
    with pytest.raises(ImageRejected):
        parse_crop(raw)


def test_crop_applies_after_exif_rotation():
    exif = Image.Exif()
    exif[0x0112] = 6
    # Orientation 6 shows the image turned 90° clockwise: left half on top.
    file = upload(split_red_blue(200, 100), "JPEG", "x.jpg", exif=exif, quality=95)

    data, ext = process_logo(file, (0, 0, 100, 90))

    img = opened(data)
    assert ext == "png"
    assert img.size == (100, 90)
    assert close_to(img.getpixel((50, 45)), RED)


def test_crop_is_clamped_to_image_bounds():
    data, _ = process_logo(upload(split_red_blue(100, 100)), (-50, -50, 100, 100))

    img = opened(data)
    assert img.size == (50, 50)
    assert close_to(img.getpixel((25, 25)), RED)


def test_cover_without_crop_cuts_centre_of_tall_image():
    img = Image.new("RGB", (1000, 1000), RED)
    img.paste(GREEN, (0, 375, 1000, 625))

    data, ext = process_cover(upload(img), None)

    out = opened(data)
    assert ext == "jpg"
    assert out.format == "JPEG"
    assert out.size == (1000, 250)
    for point in ((5, 5), (995, 5), (5, 245), (995, 245), (500, 125)):
        assert close_to(out.getpixel(point), GREEN)


def test_cover_without_crop_cuts_centre_of_wide_image():
    img = Image.new("RGB", (1000, 100), RED)
    img.paste(GREEN, (300, 0, 700, 100))

    data, _ = process_cover(upload(img), None)

    out = opened(data)
    assert out.size == (400, 100)
    assert close_to(out.getpixel((5, 50)), GREEN)
    assert close_to(out.getpixel((395, 50)), GREEN)


def test_logo_without_crop_stays_whole():
    data, _ = process_logo(upload(split_red_blue(300, 100)), None)

    assert opened(data).size == (300, 100)


def test_cover_downscales_to_max_width():
    data, _ = process_cover(upload(Image.new("RGB", (4000, 1000), RED)), None)

    assert opened(data).size == (1920, 480)


def test_cover_is_not_upscaled():
    data, _ = process_cover(upload(Image.new("RGB", (800, 200), RED)), None)

    assert opened(data).size == (800, 200)


def test_logo_downscales_longest_side():
    data, _ = process_logo(upload(Image.new("RGB", (2000, 1000), RED)), None)

    assert opened(data).size == (512, 256)


def test_logo_is_not_upscaled():
    data, _ = process_logo(upload(Image.new("RGB", (100, 50), RED)), None)

    assert opened(data).size == (100, 50)


def test_logo_keeps_transparency():
    data, _ = process_logo(upload(Image.new("RGBA", (100, 100), (0, 0, 0, 0))), None)

    out = opened(data)
    assert out.format == "PNG"
    assert out.mode == "RGBA"
    assert out.getpixel((50, 50))[3] == 0


def test_cover_flattens_transparency_on_white():
    data, _ = process_cover(upload(Image.new("RGBA", (400, 100), (0, 0, 0, 0))), None)

    out = opened(data)
    assert out.mode == "RGB"
    assert close_to(out.getpixel((200, 50)), (255, 255, 255))


@pytest.mark.parametrize("process", [process_cover, process_logo])
def test_output_has_no_exif(process):
    exif = Image.Exif()
    exif[0x010E] = "secret place"
    file = upload(Image.new("RGB", (800, 200), RED), "JPEG", "x.jpg", exif=exif)

    data, _ = process(file, None)

    assert not opened(data).getexif()
    assert b"secret place" not in data


def test_cmyk_jpeg_logo_is_accepted():
    file = upload(Image.new("CMYK", (100, 100), (0, 255, 255, 0)), "JPEG", "x.jpg")

    data, _ = process_logo(file, None)

    assert opened(data).mode == "RGBA"


def test_palette_png_logo_downscales_as_rgba():
    img = Image.new("P", (1024, 1024), 0)
    img.putpalette([255, 0, 0, 0, 0, 255])
    file = upload(img, "PNG", transparency=1)

    data, _ = process_logo(file, None)

    out = opened(data)
    assert out.mode == "RGBA"
    assert out.size == (512, 512)


def test_crop_below_minimum_is_rejected():
    with pytest.raises(ImageRejected):
        process_logo(upload(Image.new("RGB", (100, 100), RED)), (0, 0, 15, 50))


def test_crop_outside_image_is_rejected():
    with pytest.raises(ImageRejected):
        process_logo(upload(Image.new("RGB", (100, 100), RED)), (200, 200, 50, 50))


def test_small_logo_without_crop_is_rejected():
    with pytest.raises(ImageRejected):
        process_logo(upload(Image.new("RGB", (10, 10), RED)), None)


def test_small_cover_centre_cut_is_rejected():
    with pytest.raises(ImageRejected):
        process_cover(upload(Image.new("RGB", (20, 20), RED)), None)


@pytest.mark.parametrize("process", [process_cover, process_logo])
def test_gif_is_rejected(process):
    file = upload(Image.new("RGB", (800, 200), RED), "GIF", "x.gif")

    with pytest.raises(ImageRejected):
        process(file, None)


@pytest.mark.parametrize("process", [process_cover, process_logo])
def test_non_image_is_rejected(process):
    with pytest.raises(ImageRejected):
        process(SimpleUploadedFile("x.png", b"<svg></svg>"), None)


@pytest.mark.parametrize("process", [process_cover, process_logo])
def test_truncated_file_is_rejected(process):
    buffer = io.BytesIO()
    Image.effect_noise((800, 200), 50).convert("RGB").save(buffer, format="JPEG")
    file = SimpleUploadedFile("x.jpg", buffer.getvalue()[:2000])

    with pytest.raises(ImageRejected):
        process(file, None)


# --- RaceForm ---


def form_data(**overrides):
    data = {
        "name": "Header Race",
        "slug": "header-race",
        "place": "",
        "date": "2026-09-01",
        "date_end": "2026-09-02",
        "cost": 0,
        "header_image": "",
        "header_logo": "",
        "map_url": "",
        "reg_status": RegStatus.UPCOMING,
        "is_published": "on",
    }
    data.update(overrides)
    return data


def cover_file():
    return upload(split_red_blue(800, 200))


def bad_file():
    return SimpleUploadedFile("x.png", b"not an image")


def bind(files, **overrides):
    return RaceForm(data=form_data(**overrides), files=files)


@pytest.mark.django_db
def test_form_processes_valid_file():
    form = bind({"header_image_file": cover_file()})

    assert form.is_valid(), form.errors
    data, ext = form.processed_uploads["header_image"]
    assert ext == "jpg"
    assert opened(data).size == (800, 200)


@pytest.mark.django_db
def test_form_applies_crop_to_file():
    form = bind({"header_image_file": cover_file()}, header_image_crop="0,0,100,25")

    assert form.is_valid(), form.errors
    img = opened(form.processed_uploads["header_image"][0])
    assert img.size == (100, 25)
    assert close_to(img.getpixel((50, 12)), RED)


@pytest.mark.django_db
def test_form_ignores_stale_crop_without_file():
    form = bind({}, header_image_crop="garbage")

    assert form.is_valid(), form.errors
    assert form.processed_uploads == {}


@pytest.mark.django_db
def test_form_bad_crop_is_error_on_file_field():
    form = bind({"header_logo_file": cover_file()}, header_logo_crop="garbage")

    assert not form.is_valid()
    assert "header_logo_file" in form.errors


@pytest.mark.django_db
def test_form_bad_file_is_field_error():
    form = bind({"header_image_file": bad_file()})

    assert not form.is_valid()
    assert "header_image_file" in form.errors
    assert form.processed_uploads == {}


@pytest.mark.django_db
def test_form_bad_file_with_valid_url_is_still_error():
    form = bind(
        {"header_image_file": bad_file()}, header_image="https://example.com/a.jpg"
    )

    assert not form.is_valid()
    assert "header_image_file" in form.errors


@pytest.mark.django_db
def test_form_valid_file_overrides_bad_url():
    form = bind({"header_image_file": cover_file()}, header_image="not-a-url")

    assert form.is_valid(), form.errors
    assert form.cleaned_data["header_image"] == ""


@pytest.mark.django_db
def test_form_plain_url_still_saved():
    form = bind(
        {}, header_image="https://example.com/a.jpg", header_logo="/static/l.png"
    )

    assert form.is_valid(), form.errors
    race = form.save()
    assert race.header_image == "https://example.com/a.jpg"
    assert race.header_logo == "/static/l.png"


@pytest.mark.django_db
def test_form_bad_url_without_file_rejected():
    form = bind({}, header_image="not-a-url")

    assert not form.is_valid()
    assert "header_image" in form.errors


# --- RaceEditView ---


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "/media/"
    return tmp_path


def post_data(**overrides):
    data = form_data(categories_json="[]", price_tiers_json="[]")
    data.update(overrides)
    return data


def on_disk(media, value):
    return media / value.removeprefix("/media/")


def stored_files(media):
    folder = media / "race_headers"
    return sorted(folder.iterdir()) if folder.exists() else []


def edit_url(race):
    return reverse("edit_race", kwargs={"race_slug": race.slug})


@pytest.fixture
def race():
    return Race.objects.create(name="Header Race", slug="header-race")


@pytest.fixture
def editor(client, race):
    user = User.objects.create_user("editor", "editor@e.com", "p")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.ADMIN)
    client.force_login(user)
    return user


def seed(race, field="header_image"):
    value = store_upload(b"old", "jpg")
    setattr(race, field, value)
    race.save()
    return value


def fail_writes_from(monkeypatch, call_number):
    original = FileSystemStorage._save
    calls = []

    def save(self, name, content):
        calls.append(name)
        if len(calls) >= call_number:
            with open(self.path(name), "wb") as partial:
                partial.write(b"partial")
            raise OSError("disk full")
        return original(self, name, content)

    monkeypatch.setattr(FileSystemStorage, "_save", save)


@pytest.mark.django_db
def test_upload_on_create_stores_path(client, media):
    admin = User.objects.create_superuser("root", "root@e.com", "p")
    client.force_login(admin)

    resp = client.post(reverse("add_race"), post_data(header_image_file=cover_file()))

    assert resp.status_code == 302
    value = Race.objects.get(slug="header-race").header_image
    assert re.fullmatch(r"/media/race_headers/[0-9a-f]{32}\.jpg", value)
    assert on_disk(media, value).exists()


@pytest.mark.django_db
def test_upload_on_edit_stores_cover_and_logo(client, media, race, editor):
    resp = client.post(
        edit_url(race),
        post_data(header_image_file=cover_file(), header_logo_file=cover_file()),
    )

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.header_image.endswith(".jpg")
    assert race.header_logo.endswith(".png")
    assert len(stored_files(media)) == 2


@pytest.mark.django_db
def test_other_field_error_writes_nothing_and_keeps_old_value(
    client, media, race, editor
):
    old = seed(race)

    resp = client.post(
        edit_url(race), post_data(slug="", header_image_file=cover_file())
    )

    assert resp.status_code == 200
    assert resp.context["lost_uploads"] == ["header_image"]
    assert resp.context["form"]["header_image"].value() == old
    assert len(stored_files(media)) == 1

    resp = client.post(edit_url(race), post_data(header_image=old))

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.header_image == old
    assert on_disk(media, old).exists()


@pytest.mark.django_db
def test_rejected_file_keeps_old_value_without_lost_warning(
    client, media, race, editor
):
    old = seed(race)

    resp = client.post(edit_url(race), post_data(header_image_file=bad_file()))

    assert resp.status_code == 200
    assert resp.context["lost_uploads"] == []
    assert "header_image_file" in resp.context["form"].errors
    assert resp.context["form"]["header_image"].value() == old


@pytest.mark.django_db
def test_reconcile_error_removes_new_file_and_keeps_old(
    client, media, race, editor, monkeypatch
):
    old = seed(race)

    def boom(*args):
        raise ValueError("Нельзя")

    monkeypatch.setattr("apps.race.views._reconcile_promos", boom)

    resp = client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert resp.status_code == 200
    assert resp.context["lost_uploads"] == ["header_image"]
    race.refresh_from_db()
    assert race.header_image == old
    assert [p.name for p in stored_files(media)] == [on_disk(media, old).name]


@pytest.mark.django_db
def test_replace_deletes_old_file(client, media, race, editor):
    old = seed(race)

    resp = client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert resp.status_code == 302
    assert not on_disk(media, old).exists()
    assert len(stored_files(media)) == 1


@pytest.mark.django_db
def test_clearing_deletes_old_file(client, media, race, editor):
    old = seed(race)

    resp = client.post(edit_url(race), post_data(header_image=""))

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.header_image == ""
    assert not on_disk(media, old).exists()


@pytest.mark.django_db
def test_replace_keeps_file_used_by_another_race(client, media, race, editor):
    old = seed(race)
    Race.objects.create(name="Other", slug="other", header_logo=old)

    client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert on_disk(media, old).exists()


@pytest.mark.django_db
def test_replacing_external_url_touches_nothing(
    client, media, race, editor, monkeypatch
):
    race.header_image = "https://example.com/a.jpg"
    race.save()
    deleted = []
    monkeypatch.setattr(
        FileSystemStorage, "delete", lambda self, name: deleted.append(name)
    )

    resp = client.post(edit_url(race), post_data(header_image=""))

    assert resp.status_code == 302
    assert deleted == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value",
    [
        "/media/race_headers/../secret.jpg",
        "/media/race_headers/abc.jpg",
        "/media/other/" + "a" * 32 + ".jpg",
        "/media/race_headers/" + "a" * 32 + ".jpg\n",
    ],
)
def test_delete_if_unused_refuses_foreign_paths(media, value, monkeypatch):
    deleted = []
    monkeypatch.setattr(
        FileSystemStorage, "delete", lambda self, name: deleted.append(name)
    )

    delete_if_unused(value)

    assert deleted == []


@pytest.mark.django_db
def test_old_file_delete_failure_still_saves(client, media, race, editor, monkeypatch):
    seed(race)

    def denied(self, name):
        raise PermissionError("denied")

    monkeypatch.setattr(FileSystemStorage, "delete", denied)

    resp = client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert resp.status_code == 302
    race.refresh_from_db()
    assert on_disk(media, race.header_image).exists()


@pytest.mark.django_db
def test_write_failure_is_form_error_and_nothing_saved(
    client, media, race, editor, monkeypatch
):
    (media / "race_headers").mkdir()
    fail_writes_from(monkeypatch, 1)

    resp = client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert resp.status_code == 200
    assert SAVE_FAILED in resp.context["form"].non_field_errors()
    race.refresh_from_db()
    assert race.header_image == ""
    assert stored_files(media) == []


@pytest.mark.django_db
def test_second_write_failure_removes_first_file(
    client, media, race, editor, monkeypatch
):
    (media / "race_headers").mkdir()
    fail_writes_from(monkeypatch, 2)

    resp = client.post(
        edit_url(race),
        post_data(header_image_file=cover_file(), header_logo_file=cover_file()),
    )

    assert resp.status_code == 200
    assert stored_files(media) == []


@pytest.mark.django_db
def test_moderator_gets_403_and_nothing_stored(client, media, race):
    user = User.objects.create_user("mod", "mod@e.com", "p")
    RaceAdmin.objects.create(race=race, user=user, role=RaceAdmin.Role.MODERATOR)
    client.force_login(user)

    resp = client.post(edit_url(race), post_data(header_image_file=cover_file()))

    assert resp.status_code == 403
    assert stored_files(media) == []


# --- Page markup ---


@pytest.mark.django_db
def test_page_has_upload_controls(client, race, editor):
    html = client.get(edit_url(race)).content.decode()

    assert 'enctype="multipart/form-data"' in html
    for name in (
        "header_image_file",
        "header_logo_file",
        "header_image_crop",
        "header_logo_crop",
    ):
        assert f'name="{name}"' in html
    assert 'id="cropDialog"' in html
    assert "vendor/cropperjs/cropper.min.js" in html
    assert "vendor/cropperjs/cropper.min.css" in html


@pytest.mark.django_db
def test_create_page_has_upload_controls(client):
    client.force_login(User.objects.create_superuser("root", "root@e.com", "p"))

    html = client.get(reverse("add_race")).content.decode()

    assert 'name="header_image_file"' in html


@pytest.mark.django_db
def test_lost_upload_warning_only_after_failed_post(client, media, race, editor):
    warning = "Файл не сохранён, выберите его снова."

    assert warning not in client.get(edit_url(race)).content.decode()

    resp = client.post(
        edit_url(race), post_data(slug="", header_logo_file=cover_file())
    )

    assert resp.content.decode().count(warning) == 1


@pytest.mark.django_db
def test_rejected_file_error_is_rendered(client, media, race, editor):
    resp = client.post(edit_url(race), post_data(header_image_file=bad_file()))

    html = resp.content.decode()
    assert "Поддерживаются JPEG, PNG и WebP." in html
    assert "Файл не сохранён" not in html


@pytest.mark.django_db
def test_external_url_saves_unchanged(client, media, race, editor):
    url = "https://example.com/a.jpg"
    race.header_image = url
    race.save()

    resp = client.post(edit_url(race), post_data(header_image=url))

    assert resp.status_code == 302
    race.refresh_from_db()
    assert race.header_image == url
    assert stored_files(media) == []
