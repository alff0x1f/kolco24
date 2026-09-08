from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from website.models import NewsPost, PublicationKind, Race
from website.models.race import RegStatus


def create_publication(title, **kwargs):
    defaults = {
        "content": f"Текст публикации {title}",
        "publication_date": timezone.now() - timedelta(minutes=1),
    }
    defaults.update(kwargs)
    return NewsPost.objects.create(title=title, **defaults)


@pytest.mark.django_db
def test_home_is_a_real_page_and_shows_only_visible_publications(client):
    visible = create_publication("Видимый материал")
    create_publication("Черновик", is_published=False)
    create_publication(
        "Запланированный материал",
        publication_date=timezone.now() + timedelta(days=1),
    )

    response = client.get(reverse("index"))

    assert response.status_code == 200
    assert "website/home.html" in [template.name for template in response.templates]
    assert list(response.context["publications"]) == [visible]
    assert "Видимый материал" in response.content.decode()
    assert "Черновик" not in response.content.decode()


@pytest.mark.django_db
def test_home_features_nearest_open_published_race_that_has_not_ended(client):
    today = timezone.localdate()
    nearest = Race.objects.create(
        name="Ближайшая открытая",
        slug="nearest-open",
        date=today + timedelta(days=4),
        date_end=today + timedelta(days=4),
        reg_status=RegStatus.OPEN,
    )
    Race.objects.create(
        name="Следующая открытая",
        slug="next-open",
        date=today + timedelta(days=12),
        date_end=today + timedelta(days=12),
        reg_status=RegStatus.OPEN,
    )
    Race.objects.create(
        name="Регистрация ещё закрыта",
        slug="not-open-yet",
        date=today + timedelta(days=2),
        date_end=today + timedelta(days=2),
        reg_status=RegStatus.UPCOMING,
    )
    Race.objects.create(
        name="Старый забытый статус",
        slug="stale-open",
        date=today - timedelta(days=10),
        date_end=today - timedelta(days=9),
        reg_status=RegStatus.OPEN,
    )

    response = client.get(reverse("index"))

    assert response.context["featured_race"] == nearest
    assert "Ближайшая открытая" in response.content.decode()
    assert "Старый забытый статус" not in response.content.decode()


@pytest.mark.django_db
def test_home_has_no_spotlight_without_open_registration(client):
    today = timezone.localdate()
    Race.objects.create(
        name="Будущая гонка",
        slug="future-upcoming",
        date=today + timedelta(days=10),
        date_end=today + timedelta(days=10),
        reg_status=RegStatus.UPCOMING,
    )

    response = client.get(reverse("index"))

    assert response.context["featured_race"] is None
    assert "race-spotlight" not in response.content.decode()


@pytest.mark.django_db
def test_home_keeps_a_current_race_in_compact_calendar(client):
    today = timezone.localdate()
    current = Race.objects.create(
        name="Гонка идёт сейчас",
        slug="running-now",
        date=today - timedelta(days=1),
        date_end=today + timedelta(days=1),
        reg_status=RegStatus.SOLD_OUT,
    )

    response = client.get(reverse("index"))

    assert list(response.context["upcoming_races"]) == [current]
    assert "Идёт сейчас" in response.content.decode()


@pytest.mark.django_db
def test_publication_detail_uses_id_url(client):
    publication = create_publication(
        "Навигация на дистанции",
        summary="Короткий анонс",
        kind=PublicationKind.ARTICLE,
    )

    assert publication.get_absolute_url() == f"/post/{publication.pk}/"
    response = client.get(publication.get_absolute_url())

    assert response.status_code == 200
    assert response.context["publication"] == publication
    assert "Навигация на дистанции" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_published": False},
        {"publication_date": timezone.now() + timedelta(days=1)},
    ],
)
def test_unreleased_publication_returns_404(client, kwargs):
    publication = create_publication("Скрытый материал", **kwargs)

    response = client.get(f"/post/{publication.pk}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_publication_list_filters_by_kind_and_paginates(client):
    article = create_publication("Статья", kind=PublicationKind.ARTICLE)
    create_publication("Новость", kind=PublicationKind.NEWS)

    response = client.get(reverse("publication_list"), {"kind": "article"})

    assert response.status_code == 200
    assert list(response.context["publications"]) == [article]
    assert response.context["selected_kind"] == PublicationKind.ARTICLE


@pytest.mark.django_db
def test_race_list_splits_current_future_and_archive(client):
    today = timezone.localdate()
    current = Race.objects.create(
        name="Текущая",
        slug="current-race",
        date=today - timedelta(days=1),
        date_end=today,
    )
    future = Race.objects.create(
        name="Будущая",
        slug="future-race",
        date=today + timedelta(days=2),
        date_end=today + timedelta(days=2),
    )
    past = Race.objects.create(
        name="Прошедшая",
        slug="past-race",
        date=today - timedelta(days=20),
        date_end=today - timedelta(days=19),
    )
    Race.objects.create(
        name="Скрытая",
        slug="hidden-race",
        date=today + timedelta(days=3),
        date_end=today + timedelta(days=3),
        is_published=False,
    )

    response = client.get(reverse("race_list"))

    assert list(response.context["current_races"]) == [current]
    assert list(response.context["future_races"]) == [future]
    assert list(response.context["past_races"]) == [past]


@pytest.mark.django_db
def test_race_page_hides_unreleased_posts_and_links_visible_post(client):
    race = Race.objects.create(name="Гонка", slug="race-with-publications")
    visible = create_publication("Опубликованная новость", race=race)
    create_publication("Неопубликованная новость", race=race, is_published=False)

    response = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    content = response.content.decode()

    assert response.context["news_count"] == 1
    assert list(response.context["news_list"]) == [visible]
    assert visible.get_absolute_url() in content
    assert "Неопубликованная новость" not in content
