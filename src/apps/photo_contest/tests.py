import io
import json
import os
import re
import threading
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import TimestampSigner
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from PIL import ExifTags, Image

from apps.accounts.emails import build_magic_link_signature
from apps.accounts.models import EmailVerification
from apps.photo_contest import images, pairing, views
from apps.photo_contest.forms import PhotoUploadForm
from apps.photo_contest.models import (
    Comparison,
    PhotoContest,
    PhotoContestModerator,
    PhotoEntry,
    PhotoNomination,
)
from apps.photo_contest.permissions import can_moderate
from apps.photo_contest.report import build_report
from apps.photo_contest.scoring import nomination_standings, wilson_lower_bound
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


def make_voting_contest(slug="vote-2026", voting_days_left=3, **kwargs):
    """A published contest whose submissions are over and voting is open."""
    now = timezone.now()
    return make_contest(
        slug,
        days_left=-1,
        voting_until=now + timedelta(days=voting_days_left),
        **kwargs,
    )


@pytest.mark.django_db
def test_phases_follow_deadlines():
    now = timezone.now()
    submission = make_contest("s", voting_until=now + timedelta(days=10))
    voting = make_voting_contest("v")
    closed = make_voting_contest("c", voting_days_left=-1)
    no_voting = make_contest("n", days_left=-1)
    assert (submission.phase, submission.is_voting_open) == ("submission", False)
    assert (voting.phase, voting.is_voting_open) == ("voting", True)
    assert (closed.phase, closed.is_voting_open) == ("closed", False)
    assert (no_voting.phase, no_voting.is_voting_open) == ("no_voting", False)
    assert closed.is_voting_finished
    assert not voting.is_voting_finished
    assert not no_voting.is_voting_finished


@pytest.mark.django_db
def test_voting_closed_for_unpublished_contest():
    assert not make_voting_contest(published=False).is_voting_open


@pytest.mark.django_db
def test_clean_requires_voting_after_submission():
    contest = make_contest()
    contest.voting_until = contest.submission_until
    with pytest.raises(ValidationError) as exc:
        contest.full_clean()
    assert "voting_until" in exc.value.message_dict
    contest.voting_until = contest.submission_until + timedelta(seconds=1)
    contest.full_clean()


@pytest.mark.django_db
def test_comparison_limit_must_be_positive():
    contest = make_contest(max_comparisons_per_user=0)
    with pytest.raises(ValidationError) as exc:
        contest.full_clean()
    assert "max_comparisons_per_user" in exc.value.message_dict


def make_comparison(nomination, user, left, right, winner=None, **kwargs):
    return Comparison.objects.create(
        nomination=nomination,
        user=user,
        left=left,
        right=right,
        winner=winner,
        answered_at=timezone.now() if winner else None,
        **kwargs,
    )


@pytest.mark.django_db
def test_deleting_voter_cascades_comparisons(django_user_model):
    author = django_user_model.objects.create_user("a", "a@example.com", "x")
    voter = django_user_model.objects.create_user("v", "v@example.com", "x")
    nomination = make_nomination(make_voting_contest())
    left = make_entry(nomination, author, PhotoEntry.APPROVED)
    right = make_entry(nomination, author, PhotoEntry.APPROVED)
    make_comparison(nomination, voter, left, right, winner=left)
    voter.delete()
    assert not Comparison.objects.exists()


@pytest.mark.django_db
def test_compared_photo_is_protected(django_user_model):
    author = django_user_model.objects.create_user("a", "a@example.com", "x")
    voter = django_user_model.objects.create_user("v", "v@example.com", "x")
    nomination = make_nomination(make_voting_contest())
    left = make_entry(nomination, author, PhotoEntry.APPROVED)
    right = make_entry(nomination, author, PhotoEntry.APPROVED)
    make_comparison(nomination, voter, left, right)
    with pytest.raises(ProtectedError):
        right.delete()
    with pytest.raises(ProtectedError):
        author.delete()


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
    assert "На проверке: <strong>1</strong>" in content

    other = django_user_model.objects.create_user("x", "x@example.com", "x")
    client.force_login(other)
    content = client.get(contest_url(contest)).content.decode()
    assert 'id="contest-moderators"' not in content
    assert moderate_link not in content


def csrf_token_from(client, url):
    content = client.get(url).content.decode()
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', content).group(1)


def test_status_change_requires_csrf_token(moderated, user):
    contest, moderator = moderated
    entry = make_entry(make_nomination(contest), user)
    client = Client(enforce_csrf_checks=True)
    client.force_login(moderator)
    url = entry_url(contest, entry)

    assert client.post(url, {"status": "approved"}).status_code == 403
    entry.refresh_from_db()
    assert entry.status == PhotoEntry.PENDING

    token = csrf_token_from(client, moderate_url(contest))
    response = client.post(url, {"status": "approved", "csrfmiddlewaretoken": token})
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.status == PhotoEntry.APPROVED


def test_moderator_assignment_requires_csrf_token(db, superuser, user):
    contest = make_contest()
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)
    data = {"action": "add", "user": user.email}

    assert client.post(moderators_url(contest), data).status_code == 403
    assert not contest.moderators.exists()

    token = csrf_token_from(client, contest_url(contest))
    response = client.post(
        moderators_url(contest), {**data, "csrfmiddlewaretoken": token}
    )
    assert response.status_code == 302
    assert can_moderate(user, contest)


def test_status_change_on_vanished_last_page_keeps_filters(
    client, moderated, user, monkeypatch
):
    monkeypatch.setattr(views, "MODERATE_PAGE_SIZE", 1)
    contest, moderator = moderated
    nomination = make_nomination(contest)
    make_nomination(contest, "Люди", order=1)
    first = make_entry(nomination, user)
    last = make_entry(nomination, user)
    client.force_login(moderator)
    back = moderate_url(contest, status="pending", nomination=nomination.pk, page=2)

    response = client.post(
        entry_url(contest, last), {"status": "approved", "next": back}, follow=True
    )
    assert response.status_code == 200
    assert response.context["status"] == "pending"
    assert response.context["nomination"] == nomination
    assert response.context["page"].number == 1
    assert [e.pk for e in response.context["page_entries"]] == [first.pk]


# --- scoring --------------------------------------------------------------


@pytest.fixture
def voting(db, django_user_model):
    """Voting contest, one nomination, four approved photos by one author."""
    author = django_user_model.objects.create_user("author", "au@example.com", "x")
    voter = django_user_model.objects.create_user("voter", "vo@example.com", "x")
    contest = make_voting_contest()
    nomination = make_nomination(contest)
    entries = [make_entry(nomination, author, PhotoEntry.APPROVED) for _ in range(4)]
    return contest, nomination, entries, author, voter


def vote(nomination, voter, winner, loser, **kwargs):
    return make_comparison(nomination, voter, winner, loser, winner=winner, **kwargs)


def test_wilson_lower_bound_edges():
    assert wilson_lower_bound(0, 0) == 0.0
    assert 0 < wilson_lower_bound(10, 10) < 1
    assert wilson_lower_bound(0, 10) == pytest.approx(0.0, abs=1e-12)
    assert wilson_lower_bound(5, 10) < wilson_lower_bound(50, 100)
    assert wilson_lower_bound(3, 10) < wilson_lower_bound(7, 10)


def test_standings_rank_by_wins_and_share_places(voting):
    _, nomination, (a, b, c, d), _, voter = voting
    for _ in range(3):
        vote(nomination, voter, a, b)
    vote(nomination, voter, c, d)
    vote(nomination, voter, d, c)
    rows = nomination_standings(nomination)
    assert [r["entry"] for r in rows[:1]] == [a]
    assert (rows[0]["wins"], rows[0]["n"]) == (3, 3)
    by_entry = {r["entry"]: r for r in rows}
    assert by_entry[c]["place"] == by_entry[d]["place"] == 2
    assert by_entry[b]["place"] == 4
    assert (by_entry[b]["wins"], by_entry[b]["n"]) == (0, 3)


def test_standings_ignore_void_unanswered_and_other_nominations(voting):
    contest, nomination, (a, b, c, d), author, voter = voting
    vote(nomination, voter, b, a, is_void=True)
    make_comparison(nomination, voter, b, a)
    other = make_nomination(contest, "Другая")
    x = make_entry(other, author, PhotoEntry.APPROVED)
    y = make_entry(other, author, PhotoEntry.APPROVED)
    vote(other, voter, x, y)
    rows = nomination_standings(nomination)
    assert {r["entry"] for r in rows} == {a, b, c, d}
    assert all(r["n"] == 0 for r in rows)


def test_standings_skip_non_approved_photos_and_their_comparisons(voting):
    _, nomination, (a, b, c, d), author, voter = voting
    pending = make_entry(nomination, author)
    vote(nomination, voter, b, a)
    vote(nomination, voter, a, c)
    c.status = PhotoEntry.REJECTED
    c.save()
    rows = {r["entry"]: r for r in nomination_standings(nomination)}
    assert pending not in rows and c not in rows
    assert (rows[a]["wins"], rows[a]["n"]) == (0, 1)
    c.status = PhotoEntry.APPROVED
    c.save()
    rows = {r["entry"]: r for r in nomination_standings(nomination)}
    assert (rows[a]["wins"], rows[a]["n"]) == (1, 2)


# --- pairing --------------------------------------------------------------


def test_select_pair_never_offers_own_photo(voting):
    _, nomination, entries, author, voter = voting
    own = make_entry(nomination, voter, PhotoEntry.APPROVED)
    for _ in range(20):
        left, right = pairing.select_pair(nomination, voter)
        assert own not in (left, right)
    assert pairing.select_pair(nomination, author) is None  # only voter's photo left


def test_select_pair_skips_answered_pairs_including_void(voting):
    _, nomination, (a, b, c, d), _, voter = voting
    vote(nomination, voter, a, b)
    vote(nomination, voter, c, a)
    vote(nomination, voter, d, a, is_void=True)
    vote(nomination, voter, c, b)
    vote(nomination, voter, b, d)
    assert {frozenset(pairing.select_pair(nomination, voter)) for _ in range(20)} == {
        frozenset((c, d))
    }
    vote(nomination, voter, c, d)
    assert pairing.select_pair(nomination, voter) is None


def test_select_pair_starts_with_least_shown_photo(voting, django_user_model):
    _, nomination, (a, b, c, d), _, voter = voting
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    vote(nomination, other, a, b)
    vote(nomination, other, a, c)
    vote(nomination, other, b, c)
    for _ in range(20):
        assert d in pairing.select_pair(nomination, voter)


def test_select_pair_falls_back_when_least_shown_photo_is_exhausted(
    voting, django_user_model
):
    _, nomination, (a, b, c, d), _, voter = voting
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    for _ in range(3):
        vote(nomination, other, b, c)  # d stays the least shown
    vote(nomination, voter, d, a)
    vote(nomination, voter, d, b)
    vote(nomination, voter, d, c)
    pair = pairing.select_pair(nomination, voter)
    assert pair is not None and d not in pair


def test_select_pair_needs_two_candidates(voting):
    _, nomination, (a, b, c, d), _, voter = voting
    for entry in (b, c, d):
        entry.status = PhotoEntry.REJECTED
        entry.save()
    assert pairing.select_pair(nomination, voter) is None


def test_issue_pair_reuses_fresh_outstanding_row(voting):
    _, nomination, _, _, voter = voting
    first = pairing.issue_pair(nomination, voter)
    assert pairing.issue_pair(nomination, voter) == first
    assert Comparison.objects.count() == 1


def test_issue_pair_replaces_expired_row_and_keeps_it(voting):
    _, nomination, _, _, voter = voting
    first = pairing.issue_pair(nomination, voter)
    Comparison.objects.filter(pk=first.pk).update(
        created_at=timezone.now() - pairing.PAIR_TTL - timedelta(seconds=1)
    )
    second = pairing.issue_pair(nomination, voter)
    assert second.pk != first.pk
    assert Comparison.objects.count() == 2


def test_issue_pair_replaces_row_with_rejected_photo(voting):
    _, nomination, _, _, voter = voting
    first = pairing.issue_pair(nomination, voter)
    first.left.status = PhotoEntry.REJECTED
    first.left.save()
    second = pairing.issue_pair(nomination, voter)
    assert second.pk != first.pk
    assert first.left not in (second.left, second.right)


def test_unanswered_rows_are_not_shows(voting, django_user_model):
    _, nomination, (a, b, c, d), _, voter = voting
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    for _ in range(5):
        make_comparison(nomination, other, a, b)
    vote(nomination, other, c, d)
    for _ in range(20):
        assert {a, b} & set(pairing.select_pair(nomination, voter))


@pytest.mark.django_db(transaction=True)
def test_concurrent_issue_leaves_one_outstanding_row(monkeypatch, django_user_model):
    author = django_user_model.objects.create_user("a", "a@example.com", "x")
    voter = django_user_model.objects.create_user("v", "v@example.com", "x")
    nomination = make_nomination(make_voting_contest())
    for _ in range(4):
        make_entry(nomination, author, PhotoEntry.APPROVED)
    real_lock = pairing.lock_voter
    both_ready = threading.Barrier(2, timeout=10)

    def lock_in_step(user):
        both_ready.wait()
        real_lock(user)

    monkeypatch.setattr(pairing, "lock_voter", lock_in_step)
    issued = []

    def issue_one():
        try:
            issued.append(pairing.issue_pair(nomination, voter).pk)
        finally:
            connection.close()

    threads = [threading.Thread(target=issue_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert len(issued) == 2 and issued[0] == issued[1]
    assert Comparison.objects.count() == 1


# --- pair endpoint --------------------------------------------------------


def pair_url(contest, nomination):
    return reverse("photo_pair", args=[contest.slug, nomination.pk])


def post_answer(client, contest, nomination, token, winner_id, **extra):
    return client.post(
        pair_url(contest, nomination),
        data=json.dumps({"token": token, "winner_id": winner_id}),
        content_type="application/json",
        **extra,
    )


@pytest.fixture
def voter_client(client, voting):
    client.force_login(voting[4])
    return client


def test_pair_happy_path(voter_client, voting):
    contest, nomination, entries, _, voter = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    assert (pair["answered"], pair["limit"]) == (0, 30)
    ids = {e.pk for e in entries}
    assert {pair["left"]["id"], pair["right"]["id"]} <= ids
    assert pair["left"]["url"].startswith("/media/")
    response = post_answer(
        voter_client,
        contest,
        nomination,
        pair["token"],
        pair["left"]["id"],
        HTTP_USER_AGENT="Phone",
        HTTP_X_REAL_IP="10.1.2.3",
    )
    assert response.status_code == 200
    assert response.json()["answered"] == 1
    answered = Comparison.objects.get(winner__isnull=False)
    assert answered.winner_id == pair["left"]["id"]
    assert (answered.ip, answered.user_agent) == ("10.1.2.3", "Phone")
    assert answered.answered_at is not None


def test_repeated_get_returns_same_pair(voter_client, voting):
    contest, nomination, *_ = voting
    first = voter_client.get(pair_url(contest, nomination)).json()
    second = voter_client.get(pair_url(contest, nomination)).json()
    assert (first["left"], first["right"]) == (second["left"], second["right"])
    assert Comparison.objects.count() == 1


def test_double_answer_is_rejected(voter_client, voting):
    contest, nomination, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    winner = pair["left"]["id"]
    first = post_answer(voter_client, contest, nomination, pair["token"], winner)
    assert first.status_code == 200
    response = post_answer(voter_client, contest, nomination, pair["token"], winner)
    assert response.status_code == 400
    assert Comparison.objects.filter(winner__isnull=False).count() == 1


def test_expired_token_is_rejected(voter_client, voting, monkeypatch):
    contest, nomination, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    monkeypatch.setattr(pairing, "PAIR_TTL", timedelta(seconds=-1))
    response = post_answer(
        voter_client, contest, nomination, pair["token"], pair["left"]["id"]
    )
    assert response.status_code == 400


def test_fresh_token_for_old_row_is_rejected(voter_client, voting):
    contest, nomination, *_ = voting
    voter_client.get(pair_url(contest, nomination))
    row = Comparison.objects.get()
    Comparison.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - pairing.PAIR_TTL - timedelta(seconds=1)
    )
    token = TimestampSigner(salt=pairing.PAIR_TOKEN_SALT).sign(str(row.pk))
    response = post_answer(voter_client, contest, nomination, token, row.left_id)
    assert response.status_code == 400
    assert not Comparison.objects.filter(winner__isnull=False).exists()


def test_foreign_token_is_rejected(client, voting, django_user_model):
    contest, nomination, *_ = voting
    other = django_user_model.objects.create_user("o", "o@example.com", "x")
    client.force_login(other)
    pair = client.get(pair_url(contest, nomination)).json()
    client.force_login(voting[4])
    response = post_answer(
        client, contest, nomination, pair["token"], pair["left"]["id"]
    )
    assert response.status_code == 400


def test_winner_outside_pair_is_rejected(voter_client, voting):
    contest, nomination, entries, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    outsider = next(
        e.pk for e in entries if e.pk not in (pair["left"]["id"], pair["right"]["id"])
    )
    response = post_answer(voter_client, contest, nomination, pair["token"], outsider)
    assert response.status_code == 400


def test_token_of_other_nomination_is_rejected(voter_client, voting):
    contest, nomination, _, author, _ = voting
    other = make_nomination(contest, "Другая")
    for _ in range(2):
        make_entry(other, author, PhotoEntry.APPROVED)
    pair = voter_client.get(pair_url(contest, other)).json()
    response = post_answer(
        voter_client, contest, nomination, pair["token"], pair["left"]["id"]
    )
    assert response.status_code == 400


def test_rejected_photo_in_open_pair(voter_client, voting):
    contest, nomination, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    PhotoEntry.objects.filter(pk=pair["left"]["id"]).update(status=PhotoEntry.REJECTED)
    response = post_answer(
        voter_client, contest, nomination, pair["token"], pair["right"]["id"]
    )
    assert response.status_code == 400
    fresh = voter_client.get(pair_url(contest, nomination)).json()
    assert pair["left"]["id"] not in (fresh["left"]["id"], fresh["right"]["id"])


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"winner_id": 1}),
        json.dumps({"token": "x"}),
        json.dumps({"token": "x", "winner_id": "1"}),
        json.dumps({"token": "x", "winner_id": True}),
        json.dumps([1, 2]),
        json.dumps({"token": 5, "winner_id": 1}),
    ],
)
def test_malformed_body_is_400(voter_client, voting, body):
    contest, nomination, *_ = voting
    response = voter_client.post(
        pair_url(contest, nomination), data=body, content_type="application/json"
    )
    assert response.status_code == 400


def test_pair_token_and_magic_link_are_not_interchangeable(voter_client, voting):
    contest, nomination, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    row = Comparison.objects.get()
    verification, _ = EmailVerification.create_for("victim@example.com", "login")
    EmailVerification.objects.filter(pk=verification.pk).update(id=row.pk)
    verification = EmailVerification.objects.get(pk=row.pk)

    anon = Client()
    anon.get(reverse("magic_link", args=[pair["token"]]))
    assert "_auth_user_id" not in anon.session

    link = build_magic_link_signature(verification)
    response = post_answer(voter_client, contest, nomination, link, row.left_id)
    assert response.status_code == 400
    assert not Comparison.objects.filter(winner__isnull=False).exists()


def test_quota_ends_voting(voter_client, voting):
    contest, nomination, (a, b, c, d), _, voter = voting
    contest.max_comparisons_per_user = 2
    contest.save()
    vote(nomination, voter, a, b)
    vote(nomination, voter, c, d, is_void=True)
    data = voter_client.get(pair_url(contest, nomination)).json()
    assert (data["done"], data["reason"], data["answered"]) == (True, "limit", 2)


def test_answer_over_quota_is_not_stored(voter_client, voting):
    contest, nomination, (a, b, c, d), _, voter = voting
    contest.max_comparisons_per_user = 1
    contest.save()
    pair = voter_client.get(pair_url(contest, nomination)).json()
    vote(nomination, voter, c, d)
    data = post_answer(
        voter_client, contest, nomination, pair["token"], pair["left"]["id"]
    ).json()
    assert data["reason"] == "limit"
    assert Comparison.objects.filter(winner__isnull=False).count() == 1


def test_no_pairs_left(voter_client, voting):
    contest, nomination, (a, b, c, d), _, voter = voting
    for entry in (c, d):
        entry.status = PhotoEntry.REJECTED
        entry.save()
    vote(nomination, voter, a, b)
    assert (
        voter_client.get(pair_url(contest, nomination)).json()["reason"] == "no_pairs"
    )


def test_closed_voting(voter_client, voting):
    contest, nomination, *_ = voting
    pair = voter_client.get(pair_url(contest, nomination)).json()
    contest.voting_until = timezone.now() - timedelta(seconds=1)
    contest.save()
    data = post_answer(
        voter_client, contest, nomination, pair["token"], pair["left"]["id"]
    ).json()
    assert data["reason"] == "closed"
    assert not Comparison.objects.filter(winner__isnull=False).exists()
    assert voter_client.get(pair_url(contest, nomination)).json()["reason"] == "closed"


def test_pair_requires_login_and_own_nomination(client, voting):
    contest, nomination, *_ = voting
    response = client.get(pair_url(contest, nomination))
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    other = make_nomination(make_voting_contest("other"))
    client.force_login(voting[4])
    assert client.get(pair_url(contest, other)).status_code == 404
    hidden = make_voting_contest("hidden", published=False)
    hidden_nomination = make_nomination(hidden)
    assert client.get(pair_url(hidden, hidden_nomination)).status_code == 404


def test_pair_post_requires_csrf(voting):
    contest, nomination, *_ = voting
    client = Client(enforce_csrf_checks=True)
    client.force_login(voting[4])
    client.get(reverse("photo_contest", args=[contest.slug]))
    pair = client.get(pair_url(contest, nomination)).json()
    without = post_answer(
        client, contest, nomination, pair["token"], pair["left"]["id"]
    )
    assert without.status_code == 403
    token = client.cookies["csrftoken"].value
    with_header = post_answer(
        client,
        contest,
        nomination,
        pair["token"],
        pair["left"]["id"],
        HTTP_X_CSRFTOKEN=token,
    )
    assert with_header.status_code == 200


@pytest.mark.django_db(transaction=True)
def test_concurrent_answers_respect_quota(monkeypatch, django_user_model):
    author = django_user_model.objects.create_user("a", "a@example.com", "x")
    voter = django_user_model.objects.create_user("v", "v@example.com", "x")
    contest = make_voting_contest(max_comparisons_per_user=1)
    nomination = make_nomination(contest)
    a, b, c, d = (make_entry(nomination, author, PhotoEntry.APPROVED) for _ in range(4))
    rows = [
        make_comparison(nomination, voter, a, b),
        make_comparison(nomination, voter, c, d),
    ]
    signer = TimestampSigner(salt=pairing.PAIR_TOKEN_SALT)
    real_lock = pairing.lock_voter
    both_ready = threading.Barrier(2, timeout=10)

    def lock_in_step(user):
        if not getattr(threading.current_thread(), "stepped", False):
            threading.current_thread().stepped = True
            both_ready.wait()
        real_lock(user)

    monkeypatch.setattr(pairing, "lock_voter", lock_in_step)

    def answer(row):
        try:
            client = Client()
            client.force_login(voter)
            post_answer(
                client, contest, nomination, signer.sign(str(row.pk)), row.left_id
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=answer, args=(row,)) for row in rows]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert Comparison.objects.filter(winner__isnull=False).count() == 1


# --- vote page ------------------------------------------------------------


def vote_url(contest, nomination):
    return reverse("photo_vote", args=[contest.slug, nomination.pk])


def test_vote_page_redirects_anonymous_to_login_with_next(client, voting):
    contest, nomination, *_ = voting
    url = vote_url(contest, nomination)
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"] == reverse("login") + "?" + urlencode({"next": url})


def test_vote_page_renders_config_island(voter_client, voting):
    contest, nomination, *_ = voting
    other = make_nomination(contest, "Портрет", order=1)
    response = voter_client.get(vote_url(contest, nomination))
    assert response.status_code == 200
    config = json.loads(
        re.search(
            r'<script id="photoVoteConfig" type="application/json">(.*?)</script>',
            response.content.decode(),
        ).group(1)
    )
    assert config["pairUrl"] == pair_url(contest, nomination)
    assert config["contestUrl"] == reverse("photo_contest", args=[contest.slug])
    assert config["csrfToken"]
    assert vote_url(contest, other) in response.content.decode()


def test_vote_page_404s(voter_client, voting):
    contest, *_ = voting
    foreign = make_nomination(make_voting_contest("other"))
    assert voter_client.get(vote_url(contest, foreign)).status_code == 404
    hidden = make_voting_contest("hidden", published=False)
    assert (
        voter_client.get(vote_url(hidden, make_nomination(hidden))).status_code == 404
    )


# --- contest page by phase ------------------------------------------------


def test_contest_page_in_voting_phase(voter_client, voting):
    contest, nomination, (a, b, c, d), author, voter = voting
    sparse = make_nomination(contest, "Мало фото", order=1)
    make_entry(sparse, author, PhotoEntry.APPROVED)
    make_entry(sparse, author)
    vote(nomination, voter, a, b)
    vote(nomination, voter, c, d, is_void=True)
    content = voter_client.get(contest_url(contest)).content.decode()
    assert "Голосование до" in content
    assert 'name="image"' not in content
    assert vote_url(contest, nomination) in content
    assert vote_url(contest, sparse) not in content
    assert "мало фото для голосования" in content
    assert "2 из 30" in content


def test_contest_page_voting_button_for_anonymous(client, voting):
    contest, nomination, *_ = voting
    content = client.get(contest_url(contest)).content.decode()
    assert vote_url(contest, nomination) in content
    assert "из 30" not in content


def test_contest_page_after_voting(client, voting):
    contest, nomination, *_ = voting
    contest.voting_until = timezone.now() - timedelta(seconds=1)
    contest.save()
    content = client.get(contest_url(contest)).content.decode()
    assert "Голосование завершено, итоги скоро." in content
    assert vote_url(contest, nomination) not in content


def test_contest_page_submission_phase_has_no_voting(client, db, user):
    contest = make_contest(voting_until=timezone.now() + timedelta(days=30))
    nomination = make_nomination(contest)
    client.force_login(user)
    content = client.get(contest_url(contest)).content.decode()
    assert 'name="image"' in content
    assert vote_url(contest, nomination) not in content
    assert "Голосование" not in content


def test_delete_of_compared_photo_shows_message(client, voting):
    contest, nomination, (a, b, *_), author, voter = voting
    vote(nomination, voter, a, b)
    contest.submission_until = timezone.now() + timedelta(days=1)
    contest.voting_until = timezone.now() + timedelta(days=2)
    contest.save()
    client.force_login(author)
    response = client.post(
        reverse("photo_delete", args=[contest.slug, a.pk]), follow=True
    )
    assert response.status_code == 200
    assert "удалить нельзя" in response.content.decode()
    assert PhotoEntry.objects.filter(pk=a.pk).exists()


def test_contest_page_query_count(voter_client, voting, django_assert_max_num_queries):
    contest, nomination, (a, b, c, d), author, voter = voting
    for i in range(3):
        other = make_nomination(contest, f"N{i}", order=i + 1)
        x = make_entry(other, author, PhotoEntry.APPROVED)
        y = make_entry(other, author, PhotoEntry.APPROVED)
        vote(other, voter, x, y)
    vote(nomination, voter, a, b)
    with django_assert_max_num_queries(12):
        voter_client.get(contest_url(contest))


def test_list_shows_voting_contest_with_badge(client, db):
    make_voting_contest("voting")
    make_voting_contest("over", voting_days_left=-1)
    make_contest("no-voting", days_left=-1)
    response = client.get(reverse("photo_contests"))
    assert [c.slug for c in response.context["open_contests"]] == ["voting"]
    assert {c.slug for c in response.context["closed_contests"]} == {
        "over",
        "no-voting",
    }
    content = response.content.decode()
    assert "Идёт голосование" in content
    assert "Голосование завершено" in content
    assert "Приём закрыт" in content


# --- results --------------------------------------------------------------


def results_url(contest):
    return reverse("photo_results", args=[contest.slug])


def publish(client, contest, action="publish", **kwargs):
    return client.post(
        reverse("photo_results_publish", args=[contest.slug]),
        {"action": action},
        **kwargs,
    )


@pytest.fixture
def finished(voting, django_user_model):
    """``voting`` after the deadline, with a moderator and some answers."""
    contest, nomination, (a, b, c, d), author, voter = voting
    author.first_name, author.last_name = "Анна", "Фотографова"
    author.save()
    vote(nomination, voter, a, b)
    vote(nomination, voter, a, c)
    vote(nomination, voter, b, c)
    contest.voting_until = timezone.now() - timedelta(minutes=1)
    contest.save()
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    return contest, nomination, (a, b, c, d), moderator


def test_results_hidden_until_published(client, finished, user):
    contest, *_ = finished
    assert client.get(results_url(contest)).status_code == 404
    client.force_login(user)
    assert client.get(results_url(contest)).status_code == 404
    contest.is_results_published = True
    contest.save()
    assert client.get(results_url(contest)).status_code == 200
    client.logout()
    assert client.get(results_url(contest)).status_code == 200


def test_moderator_sees_unpublished_results(client, finished):
    contest, nomination, (a, b, c, d), moderator = finished
    client.force_login(moderator)
    response = client.get(results_url(contest))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Итоги не опубликованы" in content
    rows = response.context["nominations"][0].rows
    assert [r["entry"] for r in rows[:2]] == [a, b]
    assert rows[0]["author"] == "Анна Фотографова"
    assert "Побед: 2 из 2" in content


def test_results_skip_non_approved_and_show_no_status(client, finished):
    contest, nomination, (a, b, c, d), moderator = finished
    pending = make_entry(nomination, a.uploaded_by)
    c.status = PhotoEntry.REJECTED
    c.save()
    contest.is_results_published = True
    contest.save()
    response = client.get(results_url(contest))
    entries = [r["entry"] for r in response.context["nominations"][0].rows]
    assert pending not in entries and c not in entries
    content = response.content.decode()
    assert "data-status" not in content
    assert "Принято" not in content


def test_results_author_without_name_is_anonymous(client, finished):
    contest, nomination, (a, *_), moderator = finished
    author = a.uploaded_by
    author.first_name = author.last_name = ""
    author.username = "secret-local-part"
    author.save()
    client.force_login(moderator)
    content = client.get(results_url(contest)).content.decode()
    assert "Участник" in content
    assert author.username not in content


def test_publish_and_hide_results(client, finished):
    contest, *_, moderator = finished
    client.force_login(moderator)
    response = publish(client, contest)
    assert response["Location"] == contest_url(contest)
    contest.refresh_from_db()
    assert contest.is_results_published
    publish(client, contest, "hide")
    contest.refresh_from_db()
    assert not contest.is_results_published
    assert publish(client, contest, "bogus").status_code == 400


def test_publish_refused_before_voting_ends(client, voting, django_user_model):
    contest, *_ = voting
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    client.force_login(moderator)
    response = publish(client, contest, follow=True)
    assert "после голосования" in response.content.decode()
    contest.refresh_from_db()
    assert not contest.is_results_published
    no_voting = make_contest("nv", days_left=-1)
    make_moderator(no_voting, moderator)
    publish(client, no_voting)
    no_voting.refresh_from_db()
    assert not no_voting.is_results_published


def test_publish_access(client, finished, user, staff):
    contest, *_ = finished
    response = publish(client, contest)
    assert response["Location"] == reverse("login") + "?" + urlencode(
        {"next": contest_url(contest)}
    )
    client.force_login(user)
    assert publish(client, contest).status_code == 403
    client.force_login(staff)
    assert publish(client, contest).status_code == 403
    contest.refresh_from_db()
    assert not contest.is_results_published


def test_publish_requires_csrf(finished):
    contest, *_, moderator = finished
    client = Client(enforce_csrf_checks=True)
    client.force_login(moderator)
    assert publish(client, contest).status_code == 403


def test_contest_page_links_published_results(client, finished):
    contest, *_, moderator = finished
    assert results_url(contest) not in client.get(contest_url(contest)).content.decode()
    contest.is_results_published = True
    contest.save()
    assert results_url(contest) in client.get(contest_url(contest)).content.decode()
    client.force_login(moderator)
    content = client.get(contest_url(contest)).content.decode()
    assert "Скрыть итоги" in content


# --- vote report ----------------------------------------------------------


def report_url(contest, **params):
    url = reverse("photo_report", args=[contest.slug])
    return url + ("?" + urlencode(params) if params else "")


def void(client, contest, user, action="void", **extra):
    return client.post(
        reverse("photo_report_void", args=[contest.slug]),
        {"user_id": user.pk, "action": action, **extra},
    )


@pytest.fixture
def report_setup(voting, django_user_model):
    """a > b > c > d by three honest voters; ``booster`` keeps picking ``d``."""
    contest, nomination, (a, b, c, d), author, voter = voting
    honest = voter
    others = [
        django_user_model.objects.create_user(f"h{i}", f"h{i}@example.com", "x")
        for i in range(2)
    ]
    for person in (honest, *others):
        for winner, loser in [(a, b), (a, c), (a, d), (b, c), (b, d), (c, d)] * 2:
            vote(nomination, person, winner, loser, ip="10.0.0.1")
    booster = django_user_model.objects.create_user("b", "b@example.com", "x")
    for loser in (a, b, c) * 4:
        vote(nomination, booster, d, loser, ip="10.0.0.2")
    moderator = django_user_model.objects.create_user("m", "m@example.com", "x")
    make_moderator(contest, moderator)
    return contest, nomination, (a, b, c, d), honest, booster, moderator


def test_report_rows(report_setup):
    contest, nomination, (a, b, c, d), honest, booster, _ = report_setup
    rows = {r["user"]: r for r in build_report(contest)}
    assert rows[honest]["answered"] == 12
    assert rows[booster]["top_entry"] == d
    assert rows[booster]["top_count"] == 12
    assert rows[booster]["agreement"] < rows[honest]["agreement"]
    assert [r["user"] for r in build_report(contest)][0] == booster


def test_report_agreement_extremes_and_threshold(voting, django_user_model):
    contest, nomination, (a, b, c, d), author, voter = voting
    judge = django_user_model.objects.create_user("j", "j@example.com", "x")
    for winner, loser in [(a, b), (a, c), (a, d), (b, c), (b, d), (c, d)] * 3:
        vote(nomination, judge, winner, loser)
    for winner, loser in [(d, c), (c, b), (b, a)] * 4:
        vote(nomination, voter, winner, loser)
    newbie = django_user_model.objects.create_user("n", "n@example.com", "x")
    vote(nomination, newbie, a, b)
    rows = {r["user"]: r for r in build_report(contest)}
    assert rows[judge]["agreement"] > rows[voter]["agreement"]
    assert rows[voter]["agreement"] == 0
    assert rows[newbie]["agreement"] is None
    assert [r["user"] for r in build_report(contest)][-1] == newbie


def test_report_equal_scores_are_skipped(voting):
    contest, nomination, (a, b, c, d), author, voter = voting
    for _ in range(6):
        vote(nomination, voter, a, b)
        vote(nomination, voter, b, a)
    assert build_report(contest)[0]["agreement"] is None


def test_report_shared_ip_and_abandoned(voting, django_user_model):
    contest, nomination, (a, b, c, d), author, voter = voting
    twin = django_user_model.objects.create_user("t", "t@example.com", "x")
    vote(nomination, voter, a, b, ip="10.9.9.9")
    vote(nomination, twin, a, c, ip="10.9.9.9")
    vote(nomination, twin, a, d, ip="10.9.9.8")
    fresh = make_comparison(nomination, voter, c, d)
    stale = make_comparison(nomination, voter, b, d)
    Comparison.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - pairing.PAIR_TTL - timedelta(seconds=1)
    )
    assert fresh.pk
    rows = {r["user"]: r for r in build_report(contest)}
    assert rows[voter]["shared_ip_voters"] == 1
    assert rows[twin]["ip_count"] == 2
    assert rows[voter]["abandoned"] == 1


def test_report_is_void_needs_answers(voting):
    contest, nomination, (a, b, *_), author, voter = voting
    make_comparison(nomination, voter, a, b)
    assert build_report(contest)[0]["is_void"] is False


def test_report_nomination_filter(report_setup, client):
    contest, nomination, (a, *_), honest, booster, moderator = report_setup
    other = make_nomination(contest, "Другая", order=1)
    x = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
    y = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
    loner = make_comparison(other, moderator, x, y, winner=x).user
    client.force_login(moderator)
    users = {r["user"] for r in client.get(report_url(contest)).context["rows"]}
    assert loner in users
    response = client.get(report_url(contest, nomination=other.pk))
    assert {r["user"] for r in response.context["rows"]} == {loner}
    response = client.get(report_url(contest, nomination="junk"))
    assert response.context["nomination"] is None


def test_report_access(client, report_setup, user, django_user_model):
    contest, *_, moderator = report_setup
    url = report_url(contest)
    response = client.get(url)
    assert response["Location"] == reverse("login") + "?" + urlencode({"next": url})
    client.force_login(user)
    assert client.get(url).status_code == 403
    root = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    client.force_login(root)
    assert client.get(url).status_code == 403
    assert void(client, contest, user).status_code == 403
    client.force_login(moderator)
    assert client.get(url).status_code == 200


def test_void_and_unvoid(client, report_setup):
    contest, nomination, (a, b, c, d), honest, booster, moderator = report_setup
    before = {r["entry"]: r["place"] for r in nomination_standings(nomination)}
    client.force_login(moderator)
    response = void(client, contest, booster, nomination=nomination.pk)
    assert response["Location"] == report_url(contest, nomination=nomination.pk)
    assert all(c.is_void for c in Comparison.objects.filter(user=booster))
    after = {r["entry"]: r for r in nomination_standings(nomination)}
    assert after[d]["n"] == 18 and after[d]["wins"] == 0
    assert pairing.answered_count(nomination, booster) == 12
    rows = {r["user"]: r for r in build_report(contest)}
    assert rows[booster]["is_void"]
    void(client, contest, booster, "unvoid")
    assert not Comparison.objects.filter(user=booster, is_void=True).exists()
    assert {r["entry"]: r["place"] for r in nomination_standings(nomination)} == before


def test_void_is_scoped_to_contest(client, report_setup):
    contest, nomination, (a, *_), honest, booster, moderator = report_setup
    other_contest = make_voting_contest("other")
    other = make_nomination(other_contest)
    x = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
    y = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
    foreign = make_comparison(other, booster, x, y, winner=x)
    client.force_login(moderator)
    void(client, contest, booster)
    foreign.refresh_from_db()
    assert not foreign.is_void


def test_void_bad_input(client, report_setup, user):
    contest, *_, moderator = report_setup
    client.force_login(moderator)
    assert void(client, contest, user, "bogus").status_code == 400
    url = reverse("photo_report_void", args=[contest.slug])
    assert client.post(url, {"user_id": "x", "action": "void"}).status_code == 400
    response = void(client, contest, user)
    assert response.status_code == 302
    assert "нет голосов" in client.get(response["Location"]).content.decode()


def test_report_query_count(client, report_setup, django_assert_max_num_queries):
    contest, nomination, (a, *_), honest, booster, moderator = report_setup
    for i in range(3):
        other = make_nomination(contest, f"N{i}", order=i + 1)
        x = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
        y = make_entry(other, a.uploaded_by, PhotoEntry.APPROVED)
        for voter in (honest, booster, moderator):
            make_comparison(other, voter, x, y, winner=x, ip="10.0.0.3")
    client.force_login(moderator)
    with django_assert_max_num_queries(25):
        client.get(report_url(contest))
