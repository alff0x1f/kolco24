import io
import os
import re
import threading
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from PIL import ExifTags, Image

from apps.photo_contest import images, views
from apps.photo_contest.forms import PhotoUploadForm
from apps.photo_contest.models import (
    PhotoContest,
    PhotoContestModerator,
    PhotoEntry,
    PhotoNomination,
)
from apps.photo_contest.permissions import can_moderate
from apps.photo_contest.services import LimitReached, SubmissionClosed, create_entry
from apps.photo_contest.storage import PrivateStorage


@pytest.fixture(autouse=True)
def _media_roots(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.MEDIA_PRIVATE_ROOT = str(tmp_path / "private")


def make_contest(slug="photo-2026", published=True, days_left=7, **kwargs):
    return PhotoContest.objects.create(
        title="Фотоконкурс",
        slug=slug,
        submission_until=timezone.now() + timedelta(days=days_left),
        is_published=published,
        **kwargs,
    )


def make_nomination(contest, title="Пейзаж", order=0):
    return PhotoNomination.objects.create(contest=contest, title=title, order=order)


def make_entry(nomination, user, status=PhotoEntry.PENDING):
    entry = PhotoEntry(nomination=nomination, uploaded_by=user, status=status)
    entry.original.save("photo_contest/x/orig.jpg", ContentFile(b"o"), save=False)
    entry.preview.save("photo_contest/x/prev.jpg", ContentFile(b"p"), save=False)
    entry.save()
    return entry


# --- models ---------------------------------------------------------------


@pytest.mark.django_db
def test_submission_open_only_when_published_and_before_deadline():
    assert make_contest("a").is_submission_open
    assert not make_contest("b", published=False).is_submission_open
    assert not make_contest("c", days_left=-1).is_submission_open


@pytest.mark.django_db
def test_nominations_ordered_by_order_then_pk():
    contest = make_contest()
    second = make_nomination(contest, "B", order=2)
    first = make_nomination(contest, "A", order=1)
    tie = make_nomination(contest, "C", order=2)
    assert list(contest.nominations.all()) == [first, second, tie]


@pytest.mark.django_db
def test_nomination_and_contest_with_entries_are_protected(django_user_model):
    user = django_user_model.objects.create_user("u", "u@example.com", "x")
    contest = make_contest()
    nomination = make_nomination(contest)
    make_entry(nomination, user)
    with pytest.raises(ProtectedError):
        nomination.delete()
    with pytest.raises(ProtectedError):
        contest.delete()


@pytest.mark.django_db
def test_original_goes_to_private_root_without_public_url(django_user_model, settings):
    user = django_user_model.objects.create_user("u", "u@example.com", "x")
    entry = make_entry(make_nomination(make_contest()), user)
    private_path = os.path.join(settings.MEDIA_PRIVATE_ROOT, "photo_contest/x/orig.jpg")
    assert entry.original.path == private_path
    assert os.path.exists(private_path)
    assert entry.preview.path.startswith(settings.MEDIA_ROOT)
    with pytest.raises(NotImplementedError):
        entry.original.url


def make_moderator(contest, user):
    return PhotoContestModerator.objects.create(contest=contest, user=user)


@pytest.mark.django_db
def test_can_moderate_only_with_own_moderator_row(django_user_model):
    contest = make_contest("a")
    other = make_contest("b")
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    other_moderator = django_user_model.objects.create_user("o", "o@example.com", "x")
    plain = django_user_model.objects.create_user("p", "p@example.com", "x")
    superuser = django_user_model.objects.create_superuser("s", "s@example.com", "x")
    make_moderator(contest, moderator)
    make_moderator(other, other_moderator)

    assert can_moderate(moderator, contest)
    assert not can_moderate(AnonymousUser(), contest)
    assert not can_moderate(plain, contest)
    assert not can_moderate(superuser, contest)
    assert not can_moderate(other_moderator, contest)


@pytest.mark.django_db
def test_moderator_row_is_unique_per_contest(django_user_model):
    contest = make_contest()
    user = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, user)
    with pytest.raises(IntegrityError):
        make_moderator(contest, user)


# --- images ---------------------------------------------------------------


def image_bytes(fmt="JPEG", size=(400, 300), mode="RGB", noise=False, **save_kw):
    if noise:
        img = Image.frombytes(mode, size, os.urandom(size[0] * size[1] * len(mode)))
    else:
        img = Image.new(mode, size, (200, 30, 30) if mode == "RGB" else None)
    buffer = io.BytesIO()
    img.save(buffer, format=fmt, **save_kw)
    return buffer.getvalue()


def upload(data, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def exif_with(**tags):
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[getattr(ExifTags.Base, tag)] = value
    return exif


def test_small_jpeg_is_stored_byte_for_byte():
    data = image_bytes()
    file = upload(data)
    info = images.probe(file)
    assert info == images.ImageInfo("JPEG", 400, 300)
    stored, ext, width, height = images.make_original(file, info)
    assert stored == data
    assert (ext, width, height) == ("jpg", 400, 300)


@pytest.mark.parametrize(
    "fmt,ext", [("PNG", "png"), ("WEBP", "webp")], ids=["png", "webp"]
)
def test_png_and_webp_accepted(fmt, ext):
    file = upload(image_bytes(fmt), name=f"p.{ext}")
    info = images.probe(file)
    assert info.format == fmt
    assert images.make_original(file, info)[1] == ext


def test_oversized_original_is_reencoded_keeping_resolution_and_exif(monkeypatch):
    data = image_bytes(
        noise=True, quality=100, exif=exif_with(Make="Camera", Orientation=1)
    )
    monkeypatch.setattr(images, "STORE_MAX_BYTES", len(data) - 1)
    file = upload(data)
    stored, ext, width, height = images.make_original(file, images.probe(file))
    assert len(stored) <= len(data) - 1
    assert (ext, width, height) == ("jpg", 400, 300)
    with Image.open(io.BytesIO(stored)) as img:
        assert img.size == (400, 300)
        assert img.getexif()[ExifTags.Base.Make] == "Camera"


def test_original_downscaled_when_quality_steps_not_enough(monkeypatch):
    data = image_bytes(noise=True, quality=100)
    monkeypatch.setattr(images, "STORE_MAX_BYTES", 1000)
    monkeypatch.setattr(images, "FALLBACK_MAX_SIDE", 100)
    file = upload(data)
    stored, ext, width, height = images.make_original(file, images.probe(file))
    assert len(stored) <= 1000  # shrinks past FALLBACK_MAX_SIDE until it fits
    assert max(width, height) <= 100
    with Image.open(io.BytesIO(stored)) as img:
        assert img.size == (width, height)


def test_reencoded_original_applies_orientation(monkeypatch):
    data = image_bytes(
        size=(400, 300), noise=True, quality=100, exif=exif_with(Orientation=6)
    )
    monkeypatch.setattr(images, "STORE_MAX_BYTES", len(data) - 1)
    file = upload(data)
    info = images.probe(file)
    assert (info.width, info.height) == (300, 400)
    stored, _, width, height = images.make_original(file, info)
    assert (width, height) == (300, 400)
    with Image.open(io.BytesIO(stored)) as img:
        assert img.size == (300, 400)
        assert img.getexif().get(ExifTags.Base.Orientation) in (None, 1)


def test_preview_is_small_rotated_and_without_exif():
    data = image_bytes(size=(4000, 3000), exif=exif_with(Make="Camera", Orientation=6))
    preview = images.make_preview(upload(data))
    with Image.open(io.BytesIO(preview)) as img:
        assert img.format == "JPEG"
        assert img.size == (1200, 1600)
        assert not img.getexif()


def test_preview_of_transparent_png_is_on_white():
    data = image_bytes("PNG", size=(10, 10), mode="RGBA")  # fully transparent
    preview = images.make_preview(upload(data, "p.png", "image/png"))
    with Image.open(io.BytesIO(preview)) as img:
        r, g, b = img.getpixel((5, 5))
        assert min(r, g, b) > 240


def test_probe_rejects_oversized_upload(monkeypatch):
    data = image_bytes()
    monkeypatch.setattr(images, "UPLOAD_MAX_BYTES", len(data) - 1)
    with pytest.raises(images.ImageRejected, match="20 МБ"):
        images.probe(upload(data))


@pytest.mark.parametrize(
    "data",
    [image_bytes("GIF", mode="P"), b"not an image at all"],
    ids=["gif", "garbage"],
)
def test_probe_rejects_unsupported_format(data):
    with pytest.raises(images.ImageRejected, match="JPEG, PNG и WebP"):
        images.probe(upload(data))


def test_probe_rejects_too_many_pixels(monkeypatch):
    monkeypatch.setattr(images, "MAX_PIXELS", 100)
    with pytest.raises(images.ImageRejected, match="разрешение"):
        images.probe(upload(image_bytes(size=(20, 20))))


def test_truncated_jpeg_passes_probe_but_is_rejected_on_decode(monkeypatch):
    full = image_bytes(noise=True, quality=95)
    file = upload(full[: len(full) // 2])
    info = images.probe(file)
    with pytest.raises(images.ImageRejected, match="прочитать"):
        images.make_preview(file)
    monkeypatch.setattr(images, "STORE_MAX_BYTES", 10)
    with pytest.raises(images.ImageRejected, match="прочитать"):
        images.make_original(file, info)


# --- form and create service ---------------------------------------------


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user("u", "u@example.com", "x")


def test_form_scopes_nomination_to_contest(db):
    contest = make_contest()
    own = make_nomination(contest)
    foreign = make_nomination(make_contest("other"))
    data = {"caption": "hi"}
    ok = PhotoUploadForm(
        {**data, "nomination": own.pk},
        {"image": upload(image_bytes())},
        contest=contest,
    )
    assert ok.is_valid(), ok.errors
    assert ok.image_info.format == "JPEG"
    bad = PhotoUploadForm(
        {**data, "nomination": foreign.pk},
        {"image": upload(image_bytes())},
        contest=contest,
    )
    assert not bad.is_valid()
    assert "nomination" in bad.errors


def test_form_rejects_bad_file_and_long_caption(db, monkeypatch):
    contest = make_contest()
    nomination = make_nomination(contest)
    form = PhotoUploadForm(
        {"nomination": nomination.pk, "caption": "x" * 301},
        {"image": upload(b"garbage")},
        contest=contest,
    )
    assert not form.is_valid()
    assert form.errors["image"] == ["Поддерживаются JPEG, PNG и WebP."]
    assert "caption" in form.errors

    monkeypatch.setattr(images, "UPLOAD_MAX_BYTES", 10)
    form = PhotoUploadForm(
        {"nomination": nomination.pk},
        {"image": upload(image_bytes())},
        contest=contest,
    )
    assert not form.is_valid()
    assert form.errors["image"] == ["Файл больше 20 МБ."]


def create(nomination, user, data=None, caption=""):
    file = upload(data or image_bytes())
    return create_entry(nomination, user, file, images.probe(file), caption)


def stored_files(settings):
    found = []
    for root in (settings.MEDIA_ROOT, settings.MEDIA_PRIVATE_ROOT):
        for dirpath, _, names in os.walk(root):
            found += [os.path.join(dirpath, n) for n in names]
    return found


def test_create_entry_lays_files_out_by_contest_and_nomination(db, user, settings):
    nomination = make_nomination(make_contest())
    data = image_bytes()
    entry = create(nomination, user, data, caption="Закат")
    folder = f"photo_contest/{nomination.contest_id}/{nomination.pk}/"
    assert entry.original.name.startswith(folder)
    assert entry.original.name.endswith(".jpg")
    assert entry.preview.name == entry.original.name  # same uuid, other root
    assert entry.original.path.startswith(settings.MEDIA_PRIVATE_ROOT)
    assert entry.preview.path.startswith(settings.MEDIA_ROOT)
    with open(entry.original.path, "rb") as f:
        assert f.read() == data
    entry.refresh_from_db()
    assert (entry.width, entry.height, entry.size_bytes) == (400, 300, len(data))
    assert entry.status == PhotoEntry.PENDING
    assert entry.caption == "Закат"


def test_limit_is_per_nomination_and_ignores_rejected(db, user):
    contest = make_contest(max_photos_per_user=2)
    first, second = make_nomination(contest, "A"), make_nomination(contest, "B")
    create(first, user)
    rejected = create(first, user)
    rejected.status = PhotoEntry.REJECTED
    rejected.save()
    create(first, user)
    with pytest.raises(LimitReached, match="«A» уже 2 фото"):
        create(first, user)
    create(second, user)  # other nomination has its own quota


def test_failure_inside_transaction_leaves_no_files(db, user, settings, monkeypatch):
    nomination = make_nomination(make_contest())

    def boom(self, *args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(PhotoEntry, "save", boom)
    with pytest.raises(RuntimeError):
        create(nomination, user)
    assert stored_files(settings) == []


def test_undecodable_file_is_rejected_without_files(db, user, settings):
    nomination = make_nomination(make_contest())
    full = image_bytes(noise=True, quality=95)
    with pytest.raises(images.ImageRejected):
        create(nomination, user, full[: len(full) // 2])
    assert stored_files(settings) == []
    assert not PhotoEntry.objects.exists()


@pytest.mark.parametrize("change", ["close", "unpublish"])
def test_contest_closed_during_processing_is_refused(
    db, user, settings, monkeypatch, change
):
    contest = make_contest()
    nomination = make_nomination(contest)
    real_preview = images.make_preview

    def preview_then_close(file):
        data = real_preview(file)
        if change == "close":
            contest.submission_until = timezone.now() - timedelta(seconds=1)
        else:
            contest.is_published = False
        contest.save()
        return data

    monkeypatch.setattr(images, "make_preview", preview_then_close)
    with pytest.raises(SubmissionClosed):
        create(nomination, user)
    assert not PhotoEntry.objects.exists()
    assert stored_files(settings) == []


@pytest.mark.parametrize("failing", ["original", "preview"])
def test_partial_write_failure_leaves_no_files(
    db, user, settings, monkeypatch, failing
):
    nomination = make_nomination(make_contest())
    storage_cls = PrivateStorage if failing == "original" else FileSystemStorage
    real_save = storage_cls._save

    def write_half_then_fail(self, name, content):
        if not isinstance(self, storage_cls) or (
            storage_cls is FileSystemStorage and isinstance(self, PrivateStorage)
        ):
            return real_save(self, name, content)
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage_cls, "_save", write_half_then_fail)
    with pytest.raises(OSError):
        create(nomination, user)
    assert stored_files(settings) == []
    assert not PhotoEntry.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_uploads_respect_limit(settings, monkeypatch, django_user_model):
    user = django_user_model.objects.create_user("u", "u@example.com", "x")
    nomination = make_nomination(make_contest(max_photos_per_user=1))
    real_preview = images.make_preview
    both_processed = threading.Barrier(2, timeout=10)

    def preview_in_step(file):
        data = real_preview(file)
        both_processed.wait()  # both uploads reach the lock together
        return data

    monkeypatch.setattr(images, "make_preview", preview_in_step)
    outcomes = []

    def upload_one():
        try:
            create(nomination, user)
            outcomes.append("created")
        except LimitReached:
            outcomes.append("limit")
        finally:
            connection.close()

    threads = [threading.Thread(target=upload_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["created", "limit"]
    assert PhotoEntry.objects.count() == 1
    assert len(stored_files(settings)) == 2


# --- file cleanup on delete ----------------------------------------------


def test_deleting_entry_removes_files_on_commit(
    db, user, settings, django_capture_on_commit_callbacks
):
    entry = create(make_nomination(make_contest()), user)
    with django_capture_on_commit_callbacks(execute=True):
        entry.delete()
    assert stored_files(settings) == []


def test_queryset_and_user_cascade_delete_remove_files(
    db, user, settings, django_capture_on_commit_callbacks
):
    nomination = make_nomination(make_contest())
    create(nomination, user)
    with django_capture_on_commit_callbacks(execute=True):
        PhotoEntry.objects.all().delete()
    assert stored_files(settings) == []

    create(nomination, user)
    with django_capture_on_commit_callbacks(execute=True):
        user.delete()
    assert stored_files(settings) == []


def test_rolled_back_delete_keeps_files(
    db, user, settings, django_capture_on_commit_callbacks
):
    create(make_nomination(make_contest()), user)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                PhotoEntry.objects.all().delete()
                raise RuntimeError("rollback")
    assert len(stored_files(settings)) == 2
    assert PhotoEntry.objects.count() == 1


# --- pages and views ------------------------------------------------------


def contest_url(contest):
    return reverse("photo_contest", args=[contest.slug])


def post_photo(client, contest, nomination, data=None, caption=""):
    return client.post(
        reverse("photo_upload", args=[contest.slug]),
        {
            "nomination": nomination.pk,
            "caption": caption,
            "image": upload(data or image_bytes()),
        },
    )


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(
        "staff", "staff@example.com", "x", is_staff=True
    )


def test_list_shows_published_open_first(client, db):
    closed = make_contest("closed", days_left=-3)
    open_later = make_contest("later", days_left=10)
    open_soon = make_contest("soon", days_left=2)
    make_contest("hidden", published=False)
    response = client.get(reverse("photo_contests"))
    assert response.status_code == 200
    assert response.context["open_contests"] == [open_soon, open_later]
    assert response.context["closed_contests"] == [closed]
    assert b"/photo/hidden/" not in response.content


def test_unpublished_contest_hidden_except_for_staff(client, db, user, staff):
    contest = make_contest(published=False)
    assert client.get(contest_url(contest)).status_code == 404
    client.force_login(user)
    assert client.get(contest_url(contest)).status_code == 404
    client.force_login(staff)
    response = client.get(contest_url(contest))
    assert response.status_code == 200
    assert "Конкурс не опубликован" in response.content.decode()


def test_unpublished_contest_visible_to_moderator_and_superuser(
    client, db, user, django_user_model
):
    contest = make_contest(published=False)
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    client.force_login(moderator)
    response = client.get(contest_url(contest))
    assert response.status_code == 200
    assert "администраторы конкурса" in response.content.decode()

    superuser = django_user_model.objects.create_user(
        "s", "s@example.com", "x", is_superuser=True
    )
    client.force_login(superuser)
    assert client.get(contest_url(contest)).status_code == 200

    make_moderator(make_contest("other", published=False), user)
    client.force_login(user)
    assert client.get(contest_url(contest)).status_code == 404


def test_anonymous_sees_login_link_with_next(client, db):
    contest = make_contest()
    make_nomination(contest)
    content = client.get(contest_url(contest)).content.decode()
    next_param = urlencode({"next": contest_url(contest)})
    assert f'{reverse("login")}?{next_param}' in content
    assert 'name="image"' not in content


def test_upload_form_markup(client, db, user):
    contest = make_contest()
    make_nomination(contest)
    client.force_login(user)
    content = client.get(contest_url(contest)).content.decode()
    assert re.search(
        r'<form[^>]*method="post"[^>]*action="/photo/photo-2026/upload/"'
        r'[^>]*enctype="multipart/form-data"',
        content,
    )
    assert 'accept="image/jpeg,image/png,image/webp"' in content


def test_full_nomination_has_no_form(client, db, user):
    contest = make_contest(max_photos_per_user=1)
    full, free = make_nomination(contest, "Full"), make_nomination(contest, "Free")
    create(full, user)
    client.force_login(user)
    content = client.get(contest_url(contest)).content.decode()
    assert f'name="nomination" value="{full.pk}"' not in content
    assert f'name="nomination" value="{free.pk}"' in content
    assert "Лимит набран" in content


def test_nomination_shows_own_count_and_thumbnails(client, db, user, settings):
    settings.MEDIA_URL = "/media/"
    contest = make_contest(max_photos_per_user=3)
    first = make_nomination(contest, "A", 0)
    make_nomination(contest, "B", 1)
    kept = make_entry(first, user)
    rejected = make_entry(first, user, status=PhotoEntry.REJECTED)
    client.force_login(user)
    response = client.get(contest_url(contest))
    nominations = response.context["nominations"]
    assert [n.used for n in nominations] == [1, 0]
    assert nominations[0].my_entries == [rejected, kept]
    assert nominations[1].my_entries == []
    content = response.content.decode()
    assert "1 из 3" in content
    assert "0 из 3" in content
    assert "pc-thumb-rejected" in content
    assert f'href="{kept.preview.url}" target="_blank"' in content


def test_anonymous_sees_no_counters_or_forms(client, db):
    contest = make_contest()
    make_nomination(contest)
    content = client.get(contest_url(contest)).content.decode()
    assert "pc-count" not in content
    assert "<details" not in content


def test_failed_upload_reopens_its_nomination(client, db, user):
    contest = make_contest()
    make_nomination(contest, "A", 0)
    target = make_nomination(contest, "B", 1)
    client.force_login(user)
    response = post_photo(client, contest, target, data=b"garbage", caption="Рассвет")
    assert response.status_code == 200
    content = response.content.decode()
    assert content.count("<details") == 2
    assert content.count('<details class="pc-add" open>') == 1
    opened = content.index('<details class="pc-add" open>')
    assert content.index(f'value="{target.pk}"', opened) < content.index(
        "</details>", opened
    )
    assert 'value="Рассвет"' in content
    assert "Выберите файл ещё раз" in content


def test_unknown_nomination_error_is_shown_as_banner(client, db, user):
    contest = make_contest()
    make_nomination(contest)
    client.force_login(user)
    response = client.post(
        reverse("photo_upload", args=[contest.slug]),
        {"nomination": 999999, "image": upload(image_bytes())},
    )
    assert response.status_code == 200
    assert response.context["orphan_errors"] is True
    assert "Выберите номинацию из списка" in response.content.decode()


def test_closed_contest_has_no_form(client, db, user):
    contest = make_contest(days_left=-1)
    make_nomination(contest)
    client.force_login(user)
    content = client.get(contest_url(contest)).content.decode()
    assert 'name="image"' not in content
    assert "Приём фото закрыт" in content


def test_my_photos_lists_only_own_entries(client, db, user, django_user_model):
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    contest = make_contest()
    nomination = make_nomination(contest)
    mine = create(nomination, user, caption="моё")
    create(nomination, other, caption="чужое")
    create(make_nomination(make_contest("other")), user, caption="другой конкурс")
    client.force_login(user)
    response = client.get(contest_url(contest))
    assert response.context["my_entries"] == [mine]


def test_upload_anonymous_redirects_to_login(client, db):
    contest = make_contest()
    response = post_photo(client, contest, make_nomination(contest))
    assert response.status_code == 302
    assert response.url == (
        reverse("login") + "?" + urlencode({"next": contest_url(contest)})
    )
    assert not PhotoEntry.objects.exists()


def test_upload_to_unpublished_contest_is_404_even_for_staff(client, db, staff):
    contest = make_contest(published=False)
    client.force_login(staff)
    assert post_photo(client, contest, make_nomination(contest)).status_code == 404


def test_upload_to_closed_contest_is_refused(client, db, user, settings):
    contest = make_contest(days_left=-1)
    client.force_login(user)
    response = post_photo(client, contest, make_nomination(contest))
    assert response.status_code == 302
    assert not PhotoEntry.objects.exists()
    assert stored_files(settings) == []


def test_upload_success_creates_pending_entry(client, db, user):
    contest = make_contest()
    nomination = make_nomination(contest)
    client.force_login(user)
    response = post_photo(client, contest, nomination, caption="Рассвет")
    assert response.status_code == 302
    assert response.url == f"{contest_url(contest)}#nomination-{nomination.pk}"
    entry = PhotoEntry.objects.get()
    assert (entry.uploaded_by, entry.nomination, entry.caption) == (
        user,
        nomination,
        "Рассвет",
    )
    assert entry.status == PhotoEntry.PENDING
    content = client.get(contest_url(contest)).content.decode()
    assert "Фото отправлено на модерацию" in content


def test_upload_invalid_file_rerenders_with_error(client, db, user, settings):
    contest = make_contest()
    nomination = make_nomination(contest)
    client.force_login(user)
    response = post_photo(client, contest, nomination, data=b"garbage")
    assert response.status_code == 200
    assert "Поддерживаются JPEG, PNG и WebP" in response.content.decode()

    full = image_bytes(noise=True, quality=95)
    response = post_photo(client, contest, nomination, data=full[: len(full) // 2])
    assert response.status_code == 200
    assert "Не удалось прочитать изображение" in response.content.decode()
    assert stored_files(settings) == []


def test_upload_over_limit_shows_error(client, db, user):
    contest = make_contest(max_photos_per_user=1)
    nomination = make_nomination(contest, "Пейзаж")
    create(nomination, user)
    client.force_login(user)
    response = post_photo(client, contest, nomination)
    assert response.status_code == 200
    assert "уже 1 фото" in response.content.decode()
    assert PhotoEntry.objects.count() == 1


def test_owner_deletes_entry_while_open(
    client, db, user, settings, django_capture_on_commit_callbacks
):
    contest = make_contest()
    entry = create(make_nomination(contest), user)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("photo_delete", args=[contest.slug, entry.pk]))
    assert response.status_code == 302
    assert not PhotoEntry.objects.exists()
    assert stored_files(settings) == []


def test_delete_foreign_entry_is_404(client, db, user, django_user_model):
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    contest = make_contest()
    entry = create(make_nomination(contest), other)
    client.force_login(user)
    response = client.post(reverse("photo_delete", args=[contest.slug, entry.pk]))
    assert response.status_code == 404
    assert PhotoEntry.objects.filter(pk=entry.pk).exists()


def test_delete_after_close_is_refused(client, db, user):
    contest = make_contest()
    entry = create(make_nomination(contest), user)
    contest.submission_until = timezone.now() - timedelta(minutes=1)
    contest.save()
    client.force_login(user)
    response = client.post(reverse("photo_delete", args=[contest.slug, entry.pk]))
    assert response.status_code == 302
    assert PhotoEntry.objects.filter(pk=entry.pk).exists()


def test_original_needs_view_permission(client, db, user, staff, django_user_model):
    data = image_bytes()
    entry = create(make_nomination(make_contest()), user, data)
    url = reverse("photo_original", args=[entry.pk])
    assert client.get(url).status_code == 404
    client.force_login(user)
    assert client.get(url).status_code == 404
    client.force_login(staff)
    assert client.get(url).status_code == 404

    staff.user_permissions.add(Permission.objects.get(codename="view_photoentry"))
    staff = django_user_model.objects.get(pk=staff.pk)  # reset perm cache
    client.force_login(staff)
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert b"".join(response.streaming_content) == data

    admin = django_user_model.objects.create_superuser("a", "a@example.com", "x")
    client.force_login(admin)
    assert client.get(url).status_code == 200


def test_original_open_to_moderator_of_its_contest(client, db, user, django_user_model):
    contest = make_contest()
    entry = create(make_nomination(contest), user)
    url = reverse("photo_original", args=[entry.pk])
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    other_moderator = django_user_model.objects.create_user("o", "o@example.com", "x")
    make_moderator(make_contest("other"), other_moderator)

    client.force_login(moderator)
    assert client.get(url).status_code == 200
    missing = reverse("photo_original", args=[entry.pk + 1000])
    assert client.get(missing).status_code == 404

    client.force_login(other_moderator)
    assert client.get(url).status_code == 404


def test_original_missing_on_disk_is_404(client, db, user, django_user_model):
    entry = create(make_nomination(make_contest()), user)
    os.remove(entry.original.path)
    admin = django_user_model.objects.create_superuser("a", "a@example.com", "x")
    client.force_login(admin)
    assert client.get(reverse("photo_original", args=[entry.pk])).status_code == 404


# --- admin ----------------------------------------------------------------


@pytest.fixture
def admin_client_logged(client, django_user_model):
    admin = django_user_model.objects.create_superuser("a", "a@example.com", "x")
    client.force_login(admin)
    return client


def test_admin_entry_pages_render(admin_client_logged, user):
    entry = create(make_nomination(make_contest()), user, caption="Закат")
    changelist = admin_client_logged.get(
        reverse("admin:photo_contest_photoentry_changelist")
    )
    assert changelist.status_code == 200
    assert entry.preview.url in changelist.content.decode()

    change = admin_client_logged.get(
        reverse("admin:photo_contest_photoentry_change", args=[entry.pk])
    )
    content = change.content.decode()
    assert change.status_code == 200
    assert 'type="file"' not in content
    assert 'name="nomination"' not in content
    assert reverse("photo_original", args=[entry.pk]) in content


def test_admin_actions_set_status(admin_client_logged, user):
    nomination = make_nomination(make_contest())
    first, second = create(nomination, user), create(nomination, user)
    url = reverse("admin:photo_contest_photoentry_changelist")
    admin_client_logged.post(url, {"action": "approve", "_selected_action": [first.pk]})
    admin_client_logged.post(url, {"action": "reject", "_selected_action": [second.pk]})
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == PhotoEntry.APPROVED
    assert second.status == PhotoEntry.REJECTED


def test_admin_contest_page_shows_folders(admin_client_logged, settings):
    contest = make_contest()
    response = admin_client_logged.get(
        reverse("admin:photo_contest_photocontest_change", args=[contest.pk])
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert (
        os.path.join(settings.MEDIA_PRIVATE_ROOT, f"photo_contest/{contest.pk}/")
        in content
    )
    assert os.path.join(settings.MEDIA_ROOT, f"photo_contest/{contest.pk}/") in content


def test_original_that_never_fits_is_rejected(monkeypatch):
    data = image_bytes(noise=True, quality=100)
    monkeypatch.setattr(images, "STORE_MAX_BYTES", 10)
    file = upload(data)
    with pytest.raises(images.ImageRejected):
        images.make_original(file, images.probe(file))


# --- moderation -----------------------------------------------------------


@pytest.fixture
def moderated(db, django_user_model):
    contest = make_contest()
    moderator = django_user_model.objects.create_user("mod", "mod@example.com", "x")
    make_moderator(contest, moderator)
    return contest, moderator


def moderate_url(contest, **params):
    url = reverse("photo_moderate", args=[contest.slug])
    return f"{url}?{urlencode(params)}" if params else url


def entry_url(contest, entry):
    return reverse("photo_moderate_entry", args=[contest.slug, entry.pk])


def test_moderate_page_gate(client, moderated, user, staff, django_user_model):
    contest, moderator = moderated
    url = moderate_url(contest)

    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"] == reverse("login") + "?" + urlencode({"next": url})

    client.force_login(user)
    assert client.get(url).status_code == 403
    superuser = django_user_model.objects.create_superuser("s", "s@example.com", "x")
    client.force_login(superuser)
    assert client.get(url).status_code == 403
    staff.user_permissions.add(Permission.objects.get(codename="view_photoentry"))
    client.force_login(django_user_model.objects.get(pk=staff.pk))
    assert client.get(url).status_code == 403

    client.force_login(moderator)
    assert client.get(url).status_code == 200
    assert client.get("/photo/nope/moderate/").status_code == 404


def test_moderate_page_works_for_unpublished_contest(client, django_user_model, db):
    contest = make_contest(published=False)
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    client.force_login(moderator)
    assert client.get(moderate_url(contest)).status_code == 200


def test_moderate_page_filters_and_counts(client, moderated, user):
    contest, moderator = moderated
    landscape = make_nomination(contest, "Пейзаж")
    people = make_nomination(contest, "Люди", order=1)
    pending = make_entry(landscape, user)
    approved = make_entry(landscape, user, PhotoEntry.APPROVED)
    rejected = make_entry(people, user, PhotoEntry.REJECTED)
    people_pending = make_entry(people, user)
    foreign = make_entry(make_nomination(make_contest("other")), user)
    client.force_login(moderator)

    def shown(**params):
        response = client.get(moderate_url(contest, **params))
        assert response.status_code == 200
        return response, [e.pk for e in response.context["page_entries"]]

    response, pks = shown()
    assert pks == [pending.pk, people_pending.pk]
    counts = {t["value"]: t["count"] for t in response.context["tabs"]}
    assert counts == {"pending": 2, "approved": 1, "rejected": 1, "all": 4}
    assert foreign.pk not in pks

    assert shown(status="approved")[1] == [approved.pk]
    assert shown(status="rejected")[1] == [rejected.pk]
    assert len(shown(status="all")[1]) == 4
    assert shown(status="bogus")[1] == [pending.pk, people_pending.pk]

    response, pks = shown(nomination=people.pk)
    assert pks == [people_pending.pk]
    counts = {t["value"]: t["count"] for t in response.context["tabs"]}
    assert counts == {"pending": 1, "approved": 0, "rejected": 1, "all": 2}

    foreign_nomination = foreign.nomination.pk
    assert shown(nomination=foreign_nomination)[1] == [pending.pk, people_pending.pk]
    assert shown(nomination="x")[1] == [pending.pk, people_pending.pk]


def test_moderate_page_card_shows_author_and_original(client, moderated, user):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user)
    client.force_login(moderator)
    content = client.get(moderate_url(contest)).content.decode()
    assert user.email in content
    assert reverse("photo_original", args=[entry.pk]) in content
    assert 'value="approved"' in content
    assert 'value="rejected"' in content
    assert 'value="pending"' not in content


def test_moderate_page_links_keep_filters(client, moderated, user, monkeypatch):
    monkeypatch.setattr(views, "MODERATE_PAGE_SIZE", 1)
    contest, moderator = moderated
    nomination = make_nomination(contest)
    make_nomination(contest, "Люди", order=1)
    make_entry(nomination, user)
    make_entry(nomination, user)
    client.force_login(moderator)

    response = client.get(moderate_url(contest, nomination=nomination.pk))
    tab_urls = {t["value"]: t["url"] for t in response.context["tabs"]}
    assert f"nomination={nomination.pk}" in tab_urls["approved"]
    assert "status=approved" in tab_urls["approved"]
    next_url = response.context["next_url"]
    assert "page=2" in next_url
    assert "status=pending" in next_url
    assert f"nomination={nomination.pk}" in next_url

    for page in ("999", "abc"):
        response = client.get(moderate_url(contest, page=page))
        assert response.status_code == 200


@pytest.mark.parametrize(
    "start, target",
    [
        (PhotoEntry.PENDING, PhotoEntry.APPROVED),
        (PhotoEntry.PENDING, PhotoEntry.REJECTED),
        (PhotoEntry.REJECTED, PhotoEntry.PENDING),
    ],
)
def test_moderator_changes_status(client, moderated, user, start, target):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user, start)
    client.force_login(moderator)
    back = moderate_url(contest, status="all", page="2")
    response = client.post(entry_url(contest, entry), {"status": target, "next": back})
    assert response.status_code == 302
    assert response["Location"] == back
    entry.refresh_from_db()
    assert entry.status == target


def test_status_change_without_next_returns_to_moderate_page(client, moderated, user):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user)
    client.force_login(moderator)
    response = client.post(entry_url(contest, entry), {"status": "approved"})
    assert response["Location"] == moderate_url(contest)


@pytest.mark.parametrize(
    "next_url", ["https://evil.example/", "//evil.example/", "https:///evil"]
)
def test_status_change_ignores_external_next(client, moderated, user, next_url):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user)
    client.force_login(moderator)
    response = client.post(
        entry_url(contest, entry), {"status": "approved", "next": next_url}
    )
    assert response["Location"] == moderate_url(contest)


def test_restoring_rejected_photo_may_exceed_limit(client, moderated, user):
    contest, moderator = moderated
    nomination = make_nomination(contest)
    rejected = make_entry(nomination, user, PhotoEntry.REJECTED)
    for _ in range(contest.max_photos_per_user):
        make_entry(nomination, user)
    client.force_login(moderator)
    client.post(entry_url(contest, rejected), {"status": "approved"})
    rejected.refresh_from_db()
    assert rejected.status == PhotoEntry.APPROVED


def test_status_change_errors(client, moderated, user):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user)
    foreign = make_entry(make_nomination(make_contest("other")), user)
    url = entry_url(contest, entry)

    response = client.post(url, {"status": "approved"})
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))

    client.force_login(user)
    assert client.post(url, {"status": "approved"}).status_code == 403

    client.force_login(moderator)
    assert client.post(url, {"status": "bogus"}).status_code == 400
    assert client.post(url).status_code == 400
    assert client.get(url).status_code == 405
    foreign_url = reverse("photo_moderate_entry", args=[contest.slug, foreign.pk])
    assert client.post(foreign_url, {"status": "approved"}).status_code == 404

    entry.refresh_from_db()
    foreign.refresh_from_db()
    assert entry.status == PhotoEntry.PENDING
    assert foreign.status == PhotoEntry.PENDING


# --- moderator assignment -------------------------------------------------


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser("root", "root@example.com", "x")


def moderators_url(contest):
    return reverse("photo_contest_moderators", args=[contest.slug])


def block_messages(response):
    content = response.content.decode()
    block = content.split('id="contest-moderators"', 1)
    return block[1] if len(block) == 2 else ""


def test_superuser_adds_moderator_by_email_and_id(client, db, superuser, user):
    contest = make_contest()
    client.force_login(superuser)

    response = client.post(
        moderators_url(contest), {"action": "add", "user": user.email.upper()}
    )
    assert response.status_code == 302
    assert response["Location"] == contest_url(contest) + "#contest-moderators"
    assert can_moderate(user, contest)

    client.post(moderators_url(contest), {"action": "add", "user": str(user.pk)})
    assert contest.moderators.count() == 1

    client.post(moderators_url(contest), {"action": "add", "user": str(superuser.pk)})
    assert can_moderate(superuser, contest)


@pytest.mark.parametrize(
    "value, text",
    [
        ("", "Укажите email или ID"),
        ("   ", "Укажите email или ID"),
        ("x" * 255, "Слишком длинное"),
        ("ghost@example.com", "не найден"),
    ],
)
def test_add_moderator_errors(client, db, superuser, value, text):
    contest = make_contest()
    client.force_login(superuser)
    response = client.post(
        moderators_url(contest), {"action": "add", "user": value}, follow=True
    )
    assert text in block_messages(response)
    assert not contest.moderators.exists()


def test_already_assigned_moderator_message(client, db, superuser, user):
    contest = make_contest()
    make_moderator(contest, user)
    client.force_login(superuser)
    response = client.post(
        moderators_url(contest), {"action": "add", "user": user.email}, follow=True
    )
    assert "уже администратор" in block_messages(response)


def test_remove_last_moderator_keeps_block_and_message(client, db, superuser, user):
    contest = make_contest()
    make_moderator(contest, user)
    client.force_login(superuser)
    response = client.post(
        moderators_url(contest),
        {"action": "remove", "user_id": str(user.pk)},
        follow=True,
    )
    assert not contest.moderators.exists()
    block = block_messages(response)
    assert "Администратор снят." in block
    assert 'name="user"' in block
    assert "Пока никого нет" in block
    head = response.content.decode().split('id="contest-moderators"', 1)[0]
    assert "Администратор снят." not in head


@pytest.mark.parametrize("user_id", ["abc", "", "1" * 25, "-1"])
def test_remove_bad_user_id_is_harmless(client, db, superuser, user, user_id):
    contest = make_contest()
    make_moderator(contest, user)
    client.force_login(superuser)
    response = client.post(
        moderators_url(contest), {"action": "remove", "user_id": user_id}
    )
    assert response.status_code == 302
    assert contest.moderators.count() == 1


def test_remove_does_not_touch_other_contest(client, db, superuser, user):
    contest = make_contest()
    other = make_contest("other")
    make_moderator(other, user)
    client.force_login(superuser)
    client.post(moderators_url(contest), {"action": "remove", "user_id": str(user.pk)})
    assert can_moderate(user, other)


def test_moderators_view_gate(client, db, superuser, user):
    contest = make_contest(published=False)
    make_moderator(contest, user)
    url = moderators_url(contest)

    response = client.post(url, {"action": "add", "user": "a@b.c"})
    assert response["Location"] == reverse("login") + "?" + urlencode(
        {"next": contest_url(contest)}
    )
    client.force_login(user)
    assert client.post(url, {"action": "add", "user": "a@b.c"}).status_code == 403
    client.force_login(superuser)
    assert client.post(url, {"action": "bogus"}).status_code == 400
    assert client.get(url).status_code == 405
    assert client.post("/photo/nope/moderators/", {"action": "add"}).status_code == 404


def test_contest_page_moderators_block_per_role(
    client, db, superuser, user, django_user_model
):
    contest = make_contest()
    nomination = make_nomination(contest)
    moderator = django_user_model.objects.create_user("mod", "mod@example.com", "x")
    make_moderator(contest, moderator)
    make_moderator(contest, user)
    make_entry(nomination, user)
    make_entry(nomination, user, PhotoEntry.APPROVED)
    moderate_link = reverse("photo_moderate", args=[contest.slug])

    client.force_login(superuser)
    content = client.get(contest_url(contest)).content.decode()
    assert 'id="contest-moderators"' in content
    assert 'name="user"' in content
    assert user.email in content
    assert moderate_link not in content

    client.force_login(moderator)
    content = client.get(contest_url(contest)).content.decode()
    assert 'id="contest-moderators"' in content
    assert 'name="user"' not in content
    assert 'value="remove"' not in content
    assert user.email not in content
    assert moderate_link in content
    assert "1 на проверке" in content

    other = django_user_model.objects.create_user("x", "x@example.com", "x")
    client.force_login(other)
    content = client.get(contest_url(contest)).content.decode()
    assert 'id="contest-moderators"' not in content
    assert moderate_link not in content
