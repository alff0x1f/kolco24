import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from website.forms import NewsPostForm
from website.models import NewsPost, PublicationKind, Race, RaceAdmin, Team
from website.models.news import _clean_feed_html, _render_markdown
from website.models.race import Category, RegStatus
from website.views.community import owned_teams_by_race, unfinished_races


def create_publication(title, **kwargs):
    defaults = {
        "content": f"Текст публикации {title}",
        "publication_date": timezone.now() - timedelta(minutes=1),
    }
    defaults.update(kwargs)
    return NewsPost.objects.create(title=title, **defaults)


def extract_labelled_nav(response, aria_label):
    html = response.content.decode()
    match = re.search(
        rf'<nav\b[^>]*aria-label="{re.escape(aria_label)}"[^>]*>.*?</nav>',
        html,
        re.DOTALL,
    )
    assert match, f"Navigation {aria_label!r} was not rendered"
    return match.group(0)


@pytest.mark.parametrize("summary", ["", "**Авторский анонс**"])
def test_publication_template_renders_preview_once(summary):
    publication = NewsPost(
        pk=24, title="Новость", summary=summary, content_html="<p>Полный текст.</p>"
    )
    with (
        patch("website.models.news._render_markdown", wraps=_render_markdown) as render,
        patch("website.models.news._clean_feed_html", wraps=_clean_feed_html) as clean,
    ):
        render_to_string("website/_publication_post.html", {"publication": publication})

    assert render.call_count == bool(summary)
    assert clean.call_count == 2  # Before truncation and after it, once per preview.


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "Новый анонс"),
        ("content_html", "<p>Обновлённый текст.</p>"),
        ("kind", PublicationKind.ARTICLE),
    ],
)
def test_feed_preview_cache_tracks_source_changes(field, value):
    publication = NewsPost(content_html=f"<p>{'я' * 500}</p>")
    original = publication.feed_summary_html

    setattr(publication, field, value)

    assert publication.feed_summary_html != original
    assert (
        publication.feed_summary_html
        == NewsPost(
            summary=publication.summary,
            content_html=publication.content_html,
            kind=publication.kind,
        ).feed_summary_html
    )


@pytest.mark.parametrize(
    ("content_html", "summary", "read_more"),
    [
        ("<p>Короткая новость целиком.</p>", "", False),
        (f"<p>{'я' * 220}</p>", "", False),
        (f"<p>{'я' * 221}</p>", "", False),
        (f"<p>{'я' * 601}</p>", "", True),
        ("<p>Полный текст новости.</p>", "Отдельный анонс", True),
        (
            "<p>Полный <strong>текст</strong> новости.</p>",
            "Полный текст новости.",
            False,
        ),
        (
            "<p>Первая строка.</p>\n<p>Вторая строка.</p>",
            "Первая строка. Вторая строка.",
            False,
        ),
        ("<p>Карта &amp; компас</p>", "Карта & компас", False),
        ("<p>Карта &amp; компас</p>", "", False),
    ],
)
def test_publication_preview_offers_reading_only_when_text_is_missing(
    content_html, summary, read_more
):
    publication = NewsPost(
        pk=24, title="Новость", content_html=content_html, summary=summary
    )

    html = render_to_string(
        "website/_publication_post.html", {"publication": publication}
    )

    assert ('class="publication-read"' in html) is read_more
    assert f'<h3><a href="{publication.get_absolute_url()}">Новость</a></h3>' in html
    assert "&amp;amp;" not in html


@pytest.mark.parametrize(
    ("kind", "limit"), [(PublicationKind.NEWS, 600), (PublicationKind.ARTICLE, 220)]
)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_feed_preview_limits_preserve_compact_metadata(kind, limit, offset):
    content = "я" * (limit + offset)
    publication = NewsPost(
        pk=1, title="Публикация", kind=kind, content_html=f"<p>{content}</p>"
    )

    html = render_to_string(
        "website/_publication_post.html", {"publication": publication}
    )

    assert len(publication.feed_summary) == min(len(content), limit)
    assert len(publication.card_summary) == min(len(content), 220)
    assert (publication.feed_summary == content) is (offset <= 0)
    assert ('class="publication-read"' in html) is (offset > 0)
    assert (
        '<div class="publication-post__summary">'
        f"<p>{publication.feed_summary}</p></div>" in html
    )


@pytest.mark.parametrize("kind", [PublicationKind.NEWS, PublicationKind.ARTICLE])
def test_feed_preview_preserves_editor_summary(kind):
    summary = "Авторский анонс. " * 50
    publication = NewsPost(
        pk=1,
        title="Публикация",
        kind=kind,
        summary=summary,
        content_html="<p>Полный текст.</p>",
    )

    html = render_to_string(
        "website/_publication_post.html", {"publication": publication}
    )

    assert publication.feed_summary == summary.strip()
    assert publication.card_summary == summary.strip()
    assert summary.strip() in html
    assert 'class="publication-read"' in html


@pytest.mark.parametrize("use_editor_summary", [False, True])
def test_feed_preview_preserves_links_and_line_breaks(use_editor_summary):
    content_html = (
        '<p><a href="https://example.com/map">Карта</a><br>'
        "Старт в <strong>10:00</strong>.</p><p>Ждём на поляне.</p>"
    )
    publication = NewsPost(
        pk=1,
        title="Старт",
        content_html=content_html,
        summary=content_html if use_editor_summary else "",
    )

    html = render_to_string(
        "website/_publication_post.html", {"publication": publication}
    )

    assert 'href="https://example.com/map"' in html
    assert "</a><br>Старт в <strong>10:00</strong>.</p>" in html
    assert "<p>Ждём на поляне.</p>" in html
    assert "&lt;br" not in html
    assert 'class="publication-read"' not in html


def test_feed_preview_renders_markdown_in_editor_summary():
    publication = NewsPost(
        pk=1,
        title="Старт",
        content_html="<p>Подробности старта.</p>",
        summary="[Карта](https://example.com/map)  \nСтарт в **10:00**.",
    )

    html = render_to_string(
        "website/_publication_post.html", {"publication": publication}
    )

    assert 'href="https://example.com/map"' in html
    assert "<br>" in html
    assert "<strong>10:00</strong>" in html


@pytest.mark.parametrize(
    ("kind", "limit"), [(PublicationKind.NEWS, 600), (PublicationKind.ARTICLE, 220)]
)
def test_feed_preview_does_not_truncate_short_text_with_entities(kind, limit):
    publication = NewsPost(
        pk=1,
        title="Новость",
        kind=kind,
        content_html=f"<p>{'я' * (limit - 1)}&amp;</p>",
    )

    assert publication.feed_summary_html.endswith("&amp;</p>")
    assert not publication.has_more_content


@pytest.mark.parametrize(
    ("kind", "limit"), [(PublicationKind.NEWS, 600), (PublicationKind.ARTICLE, 220)]
)
def test_feed_preview_closes_link_when_truncating(kind, limit):
    publication = NewsPost(
        pk=1,
        title="Новость",
        kind=kind,
        content_html=f'<p><a href="/races/">{"я" * (limit + 100)}</a></p>',
    )

    html = publication.feed_summary_html

    assert 'href="/races/"' in html
    assert html.endswith("…</a></p>")
    assert publication.has_more_content


@pytest.mark.parametrize("use_editor_summary", [False, True])
def test_feed_preview_removes_unsafe_html(use_editor_summary):
    unsafe_html = (
        '<p onclick="alert(1)">Текст<br>'
        '<a href="javascript:alert(1)">Ссылка</a>'
        "<script>alert(1)</script></p>"
    )
    publication = NewsPost(
        pk=1,
        title="Новость",
        content_html=unsafe_html,
        summary=unsafe_html if use_editor_summary else "",
    )

    html = publication.feed_summary_html

    assert "<br>" in html
    assert "<script" not in html
    assert "onclick" not in html
    assert "javascript:" not in html


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
def test_home_paginates_news_and_articles_together(client):
    publication_date = timezone.now() - timedelta(days=1)
    publications = [
        create_publication(
            f"Материал {index}",
            kind=(PublicationKind.NEWS if index % 2 else PublicationKind.ARTICLE),
            publication_date=publication_date,
        )
        for index in range(10)
    ]

    first_page = client.get(reverse("index"))
    second_page = client.get(reverse("index"), {"page": 2})

    assert list(first_page.context["publications"]) == list(reversed(publications[1:]))
    assert list(second_page.context["publications"]) == [publications[0]]
    assert 'href="?page=2#publications"' in first_page.content.decode()
    assert 'href="?page=1#publications"' in second_page.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("page", "expected_page"),
    [("invalid", 1), ("999", 2)],
)
def test_home_pagination_handles_invalid_page_numbers(client, page, expected_page):
    for index in range(10):
        create_publication(f"Материал {index}")

    response = client.get(reverse("index"), {"page": page})

    assert response.status_code == 200
    assert response.context["page_obj"].number == expected_page


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["index", "article_list", "race_list"])
def test_sections_feature_nearest_open_or_upcoming_published_race_that_has_not_ended(
    client,
    route,
):
    today = timezone.localdate()
    Race.objects.create(
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

    Race.objects.create(
        name="Скрытая ближайшая гонка",
        slug="hidden-nearest",
        date=today,
        date_end=today,
        is_published=False,
    )

    response = client.get(reverse(route))

    assert response.context["featured_race"].slug == "not-open-yet"
    assert "Регистрация скоро откроется" in response.content.decode()
    html = response.content.decode()
    spotlight = re.search(r'<section class="race-spotlight".*?</section>', html, re.S)
    assert spotlight
    assert "Старый забытый статус" not in spotlight.group()
    assert "Скрытая ближайшая гонка" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["index", "article_list", "race_list"])
def test_sections_upcoming_registration_spotlight_has_no_registration_link(
    client, route
):
    today = timezone.localdate()
    Race.objects.create(
        name="Будущая гонка",
        slug="future-upcoming",
        date=today + timedelta(days=10),
        date_end=today + timedelta(days=10),
        reg_status=RegStatus.UPCOMING,
    )

    response = client.get(reverse(route))

    html = response.content.decode()
    assert response.context["featured_race"].slug == "future-upcoming"
    assert "race-spotlight" in html
    assert "Регистрация скоро откроется" in html
    assert "Зарегистрироваться" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["index", "article_list", "race_list"])
def test_sections_have_no_spotlight_without_open_or_upcoming_registration(
    client, route
):
    today = timezone.localdate()
    Race.objects.create(
        name="Гонка без мест",
        slug="future-sold-out",
        date=today + timedelta(days=10),
        date_end=today + timedelta(days=10),
        reg_status=RegStatus.SOLD_OUT,
    )

    response = client.get(reverse(route))

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
    html = response.content.decode()
    assert "Навигация на дистанции" in html
    expected_og_url = (
        f'<meta property="og:url" content="https://kolco24.ru'
        f'{publication.get_absolute_url()}">'
    )
    assert expected_og_url in html


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
@pytest.mark.parametrize("kind", [PublicationKind.NEWS, PublicationKind.ARTICLE])
def test_unpublished_race_posts_stay_private_until_race_is_published(client, kind):
    race = Race.objects.create(
        name="Неанонсированная гонка", slug="unannounced-race", is_published=False
    )
    hidden = create_publication("Секретный анонс", race=race, kind=kind)
    standalone = create_publication("Материал без гонки", kind=kind)
    public_race = Race.objects.create(name="Открытая гонка", slug="announced-race")
    visible = create_publication("Открытый анонс", race=public_race, kind=kind)
    create_publication("Черновик", race=race, kind=kind, is_published=False)
    create_publication(
        "Запланировано",
        race=race,
        kind=kind,
        publication_date=timezone.now() + timedelta(days=1),
    )
    routes = ["index", "article_list"] if kind == PublicationKind.ARTICLE else ["index"]

    for published in [False, True, False]:
        race.is_published = published
        race.save(update_fields=["is_published"])
        expected = {standalone, visible, hidden} if published else {standalone, visible}
        for route in routes:
            response = client.get(reverse(route))
            assert response.status_code == 200
            assert set(response.context["publications"]) == expected
            assert response.context["page_obj"].paginator.count == len(expected)
            if not published:
                html = response.content.decode()
                assert hidden.title not in html
                assert race.name not in html
                assert race.slug not in html

        response = client.get(hidden.get_absolute_url())
        assert response.status_code == (200 if published else 404)
        if not published:
            assert hidden.title not in response.content.decode()


@pytest.mark.django_db
def test_authenticated_visitor_cannot_read_unpublished_race_post(
    client, django_user_model
):
    race = Race.objects.create(
        name="Черновик гонки", slug="draft-race", is_published=False
    )
    publication = create_publication("Новость черновика", race=race)
    user = django_user_model.objects.create_user(username="visitor")
    client.force_login(user)

    assert client.get(publication.get_absolute_url()).status_code == 404
    assert list(client.get(reverse("index")).context["publications"]) == []


@pytest.mark.django_db
def test_article_catalog_route(client):
    assert reverse("article_list") == "/articles/"

    response = client.get(reverse("article_list"))

    assert response.status_code == 200
    assert response.context["catalog_title"] == "Статьи"
    assert response.context["catalog_description"]
    assert response.context["section_tab"] == "articles"


@pytest.mark.django_db
def test_article_catalog_filters_out_news(client):
    article = create_publication("Статья", kind=PublicationKind.ARTICLE)
    create_publication("Новость", kind=PublicationKind.NEWS)

    articles_response = client.get(reverse("article_list"))

    assert list(articles_response.context["publications"]) == [article]


@pytest.mark.parametrize("path", ["/posts/", "/news/"])
def test_removed_publication_catalog_routes_do_not_exist(path):
    with pytest.raises(Resolver404):
        resolve(path)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "active_url_name"),
    [
        ("index", "index"),
        ("article_list", "article_list"),
        ("race_list", "race_list"),
    ],
)
def test_community_section_tabs_mark_current_page(
    client,
    url_name,
    active_url_name,
):
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    nav = extract_labelled_nav(response, "Разделы сайта")
    current_links = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', nav)

    assert len(current_links) == 1
    assert 'class="section-tab is-active"' in current_links[0]
    assert f'href="{reverse(active_url_name)}"' in current_links[0]


@pytest.mark.django_db
def test_community_section_tabs_link_to_each_standalone_route(client):
    response = client.get(reverse("index"))
    nav = extract_labelled_nav(response, "Разделы сайта")
    hrefs = re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>', nav)

    assert hrefs == [
        reverse("index"),
        reverse("article_list"),
        reverse("race_list"),
    ]


@pytest.mark.django_db
def test_article_catalog_pagination_does_not_render_kind_query(client):
    for index in range(10):
        create_publication(f"Статья {index}", kind=PublicationKind.ARTICLE)

    response = client.get(reverse("article_list"))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'href="?page=2"' in html
    assert "?kind=" not in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "catalog_url_name"),
    [
        (PublicationKind.NEWS, None),
        (PublicationKind.ARTICLE, "article_list"),
    ],
)
def test_publication_detail_keeps_category_breadcrumbs_without_section_tabs(
    client, kind, catalog_url_name
):
    publication = create_publication("Материал без общей панели", kind=kind)

    response = client.get(publication.get_absolute_url())
    html = response.content.decode()
    breadcrumbs = extract_labelled_nav(response, "Хлебные крошки")

    assert 'aria-label="Разделы сайта"' not in html
    assert 'class="section-tabs"' not in html
    expected_url = (
        reverse(catalog_url_name)
        if catalog_url_name
        else f'{reverse("index")}#publications'
    )
    assert f'href="{expected_url}"' in breadcrumbs
    assert f'href="{expected_url}"' in html


@pytest.mark.django_db
def test_dark_navbar_does_not_duplicate_publication_and_race_links(client):
    response = client.get(reverse("index"))
    html = response.content.decode()
    header_match = re.search(
        r'<header\b[^>]*class="nav"[^>]*>.*?</header>', html, re.DOTALL
    )

    assert header_match, "Dark site navbar was not rendered"
    navbar = header_match.group(0)
    assert 'class="nav-links"' not in navbar
    assert f'href="{reverse("race_list")}"' not in navbar


@pytest.mark.django_db
def test_race_list_splits_current_future_and_archive(client):
    today = timezone.localdate()
    current = Race.objects.create(
        name="Текущая",
        slug="current-race",
        reg_status=RegStatus.SOLD_OUT,
        date=today - timedelta(days=1),
        date_end=today,
    )
    future = Race.objects.create(
        name="Будущая",
        slug="future-race",
        reg_status=RegStatus.SOLD_OUT,
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
@pytest.mark.parametrize("route", ["index", "article_list", "race_list"])
def test_sections_open_spotlight_links_to_registration(client, route):
    today = timezone.localdate()
    race = Race.objects.create(
        name="Открытая гонка",
        slug="open-spotlight",
        date=today + timedelta(days=1),
        date_end=today + timedelta(days=1),
        reg_status=RegStatus.OPEN,
    )

    html = client.get(reverse(route)).content.decode()

    assert f'href="{reverse("add_team", args=[race.slug])}"' in html
    assert f'href="{reverse("race", args=[race.slug])}"' in html
    assert html.count('id="featured-race-title"') == 1
    assert html.index('class="section-tabs"') < html.index('class="race-spotlight"')


@pytest.mark.django_db
@pytest.mark.parametrize("starts_in", [-1, 1])
@pytest.mark.parametrize("has_other_future", [False, True])
def test_race_catalog_shows_featured_race_once_and_keeps_other_starts(
    client, starts_in, has_other_future
):
    today = timezone.localdate()
    featured = Race.objects.create(
        name="Главный старт",
        slug="featured-once",
        date=today + timedelta(days=starts_in),
        date_end=today + timedelta(days=1),
    )
    other = None
    if has_other_future:
        other = Race.objects.create(
            name="Следующий старт",
            slug="other-start",
            date=today + timedelta(days=10),
            date_end=today + timedelta(days=10),
        )

    response = client.get(reverse("race_list"))
    html = response.content.decode()

    assert response.context["featured_race"] == featured
    assert html.count(featured.name) == 1
    assert list(response.context["current_races"]) == []
    assert list(response.context["future_races"]) == ([other] if other else [])
    assert 'id="current-races-title"' not in html
    assert 'id="archive-title"' in html
    if has_other_future:
        assert html.count(f'<h3><a href="{reverse("race", args=[other.slug])}">') == 1
        assert ("Другие будущие старты" in html) == (starts_in > 0)
    elif starts_in > 0:
        assert 'id="future-races-title"' not in html
        assert "Новые соревнования пока не объявлены" not in html
    else:
        assert "Новые соревнования пока не объявлены" in html


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


@pytest.mark.django_db
@pytest.mark.parametrize("hidden_by", ["draft", "future", "race"])
@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("anonymous", False),
        ("visitor", False),
        ("staff", False),
        ("other_race", False),
        ("admin", True),
        ("moderator", True),
        ("editor", True),
        ("superuser", True),
    ],
)
def test_publication_preview_requires_editor_rights(
    client, django_user_model, hidden_by, role, allowed
):
    race = Race.objects.create(
        name="Гонка", slug="preview-race", is_published=hidden_by != "race"
    )
    publication = create_publication(
        "Скрытая статья",
        race=race,
        kind=PublicationKind.ARTICLE,
        is_published=hidden_by != "draft",
        publication_date=timezone.now()
        + timedelta(days=1 if hidden_by == "future" else -1),
    )
    if role != "anonymous":
        user = django_user_model.objects.create_user(
            username=role, is_staff=role == "staff", is_superuser=role == "superuser"
        )
        if role in ("admin", "moderator", "other_race"):
            managed_race = race
            if role == "other_race":
                managed_race = Race.objects.create(name="Другая", slug="other-race")
            RaceAdmin.objects.create(
                user=user,
                race=managed_race,
                role=(
                    RaceAdmin.Role.MODERATOR
                    if role == "moderator"
                    else RaceAdmin.Role.ADMIN
                ),
            )
        if role == "editor":
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="website", codename="change_newspost"
                )
            )
        client.force_login(user)

    response = client.get(publication.get_absolute_url())

    assert response.status_code == (200 if allowed else 404)
    if allowed:
        assert response.context["is_preview"] is True
        assert "Предпросмотр" in response.content.decode()
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Robots-Tag"] == "noindex, nofollow"
    for route in ("index", "article_list"):
        assert list(client.get(reverse(route)).context["publications"]) == []


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["editor", "superuser"])
def test_global_editor_can_preview_standalone_draft(client, django_user_model, role):
    publication = create_publication("Черновик без гонки", is_published=False)
    user = django_user_model.objects.create_user(
        username=role, is_superuser=role == "superuser"
    )
    if role == "editor":
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="website", codename="change_newspost"
            )
        )
    client.force_login(user)
    assert client.get(publication.get_absolute_url()).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("published", [False, True])
def test_race_form_saves_drafts_and_scheduled_articles(
    client, django_user_model, published
):
    race = Race.objects.create(name="Гонка", slug="form-race")
    user = django_user_model.objects.create_user(username="race-editor")
    RaceAdmin.objects.create(race=race, user=user)
    client.force_login(user)
    date = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
        second=0, microsecond=0
    )
    page = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    for field in ("kind", "is_published", "publication_date"):
        assert f'name="{field}"' in page.content.decode()
    data = {
        "title": "Запланированная статья",
        "content": "**Подробности**",
        "kind": PublicationKind.ARTICLE,
        "publication_date": date.strftime("%Y-%m-%dT%H:%M"),
    }
    if published:
        data["is_published"] = "on"

    response = client.post(f"/race/{race.slug}/post/add/", data)

    publication = NewsPost.objects.get(race=race)
    assert publication.kind == PublicationKind.ARTICLE
    assert publication.is_published is published
    assert publication.publication_date == date
    assert response.status_code == 302
    assert response["Location"] == publication.get_absolute_url()
    assert client.get(response["Location"]).context["is_preview"] is True
    assert not NewsPost.objects.visible().exists()
    client.logout()
    assert client.get(publication.get_absolute_url()).status_code == 404


def test_publication_form_preserves_invalid_scheduling_input():
    form = NewsPostForm(
        data={
            "title": "Черновик",
            "content": "Текст",
            "kind": "article",
            "publication_date": "не дата",
        }
    )
    assert not form.is_valid()
    assert "publication_date" in form.errors
    assert form["publication_date"].value() == "не дата"
    assert form["kind"].value() == "article"
    assert form["is_published"].value() is False


@pytest.mark.django_db
def test_race_feed_uses_shared_visibility_and_stable_order(
    client, django_assert_num_queries
):
    race = Race.objects.create(name="Гонка", slug="ordered-feed")
    date = timezone.now() - timedelta(days=1)
    posts = [
        create_publication(
            f"Статья {i}",
            race=race,
            kind=PublicationKind.ARTICLE,
            publication_date=date,
        )
        for i in range(3)
    ]
    create_publication("Черновик", race=race, is_published=False)
    create_publication(
        "Будущая", race=race, publication_date=timezone.now() + timedelta(days=1)
    )
    expected = list(reversed(posts))
    for route in ("index", "article_list"):
        assert list(client.get(reverse(route)).context["publications"]) == expected
    response = client.get(reverse("race", kwargs={"race_slug": race.slug}))
    assert response.context["news_list"] == expected
    assert response.context["news_count"] == 3
    with django_assert_num_queries(0):
        assert all(
            post.race.name == race.name for post in response.context["news_list"]
        )
    race.is_published = False
    race.save(update_fields=["is_published"])
    from apps.race.views import RacePageView

    assert RacePageView.build_context(race)["news_list"] == []


def _unfinished_races():
    """Same queryset the home view feeds the panel, so the two can't drift."""
    return unfinished_races(timezone.localdate())


def _extract(pattern, html, what):
    """First capture group of ``pattern``, with a readable failure message."""
    match = re.search(pattern, html, re.S)
    assert match is not None, f"{what} was not rendered"
    return match.group(1)


def _panel(html):
    """The owned-teams section alone, so assertions can't pass on other blocks."""
    return _extract(
        r'(<section class="my-teams".*?</section>)', html, "owned-teams panel"
    )


def create_owned_race(slug, days=3, **kwargs):
    today = timezone.localdate()
    defaults = {
        "name": f"Гонка {slug}",
        "date": today + timedelta(days=days),
        "date_end": today + timedelta(days=days),
    }
    defaults.update(kwargs)
    return Race.objects.create(slug=slug, **defaults)


def create_owned_category(race, **kwargs):
    defaults = {"code": "12h", "short_name": "12ч", "name": "12 часов", "order": 0}
    defaults.update(kwargs)
    return Category.objects.create(race=race, **defaults)


def create_owned_team(owner, category, **kwargs):
    defaults = {"ucount": 3, "paid_people": 3, "start_number": "7", "city": "Уфа"}
    defaults.update(kwargs)
    return Team.objects.create(owner=owner, category2=category, **defaults)


@pytest.mark.django_db
def test_owned_teams_by_race_collects_team_card_fields(django_user_model):
    user = django_user_model.objects.create_user(
        username="owner", first_name="Иван", last_name="Петров"
    )
    race = create_owned_race("owned-fields", is_teams_editable=True)
    team = create_owned_team(
        user, create_owned_category(race), teamname="Лесные коты", start_number="18"
    )

    groups = owned_teams_by_race(user, _unfinished_races())

    assert len(groups) == 1
    assert groups[0]["race"] == race
    assert groups[0]["teams"] == [
        {
            "id": team.id,
            "name": "Лесные коты",
            "number": "18",
            "category": "12ч",
            "city": "Уфа",
            "participants": 3,
            "url": reverse("edit_team", args=[team.id]),
            "action_label": "Редактировать команду",
            "can_change": True,
            "needs_payment": False,
            "paid_people": 3.0,
        }
    ]


@pytest.mark.django_db
def test_owned_teams_by_race_ignores_anonymous_and_other_owners(django_user_model):
    stranger = django_user_model.objects.create_user(username="stranger")
    user = django_user_model.objects.create_user(username="viewer")
    race = create_owned_race("owned-strangers")
    create_owned_team(stranger, create_owned_category(race), teamname="Чужая")

    assert owned_teams_by_race(AnonymousUser(), _unfinished_races()) == []
    assert owned_teams_by_race(None, _unfinished_races()) == []
    assert owned_teams_by_race(user, _unfinished_races()) == []


@pytest.mark.django_db
def test_owned_teams_by_race_marks_closed_editing_as_view_only(django_user_model):
    user = django_user_model.objects.create_user(username="locked-owner")
    race = create_owned_race("owned-locked", is_teams_editable=False)
    create_owned_team(user, create_owned_category(race), teamname="Закрытая")

    (group,) = owned_teams_by_race(user, _unfinished_races())

    assert group["teams"][0]["can_change"] is False
    assert group["teams"][0]["action_label"] == "Посмотреть команду"


@pytest.mark.django_db
def test_owned_teams_by_race_groups_races_in_date_order(django_user_model):
    user = django_user_model.objects.create_user(username="multi-owner")
    later = create_owned_race("owned-later", days=20)
    sooner = create_owned_race("owned-sooner", days=2)
    create_owned_team(user, create_owned_category(later), teamname="Поздняя")
    create_owned_team(user, create_owned_category(sooner), teamname="Ранняя")

    groups = owned_teams_by_race(user, _unfinished_races())

    assert [group["race"] for group in groups] == [sooner, later]
    assert [group["teams"][0]["name"] for group in groups] == ["Ранняя", "Поздняя"]


@pytest.mark.django_db
def test_owned_teams_by_race_falls_back_to_generated_name(django_user_model):
    user = django_user_model.objects.create_user(
        username="nameless", first_name="Иван", last_name="Петров"
    )
    race = create_owned_race("owned-nameless")
    team = create_owned_team(user, create_owned_category(race), teamname="")

    (group,) = owned_teams_by_race(user, _unfinished_races())

    assert group["teams"][0]["name"] == f"Без названия {team.id} (Петров Иван)"


@pytest.mark.django_db
def test_owned_teams_by_race_keeps_one_group_per_race_in_team_order(
    django_user_model,
):
    user = django_user_model.objects.create_user(username="many-teams")
    race = create_owned_race("owned-many")
    first = create_owned_category(race, code="12h", order=0)
    second = create_owned_category(race, code="24h", short_name="24ч", order=1)
    create_owned_team(user, first, teamname="A1", start_number="1")
    create_owned_team(user, first, teamname="A2", start_number="10")
    create_owned_team(user, second, teamname="B", start_number="2")

    groups = owned_teams_by_race(user, _unfinished_races())

    assert len(groups) == 1
    # start_number is a CharField, so "1" < "10" lexicographically — deliberate.
    assert [team["name"] for team in groups[0]["teams"]] == ["A1", "A2", "B"]


@pytest.mark.django_db
def test_owned_teams_by_race_skips_deleted_teams(django_user_model):
    user = django_user_model.objects.create_user(username="deleted-owner")
    race = create_owned_race("owned-deleted")
    team = create_owned_team(user, create_owned_category(race), teamname="Удалённая")
    Team.objects.filter(pk=team.pk).update(is_deleted=True)

    assert owned_teams_by_race(user, _unfinished_races()) == []


@pytest.mark.django_db
def test_owned_teams_by_race_lets_a_superuser_edit_a_locked_race(django_user_model):
    user = django_user_model.objects.create_superuser(
        username="super-owner", email="super-owner@example.com", password="x"
    )
    race = create_owned_race("owned-super", is_teams_editable=False)
    create_owned_team(user, create_owned_category(race), teamname="Админская")

    (group,) = owned_teams_by_race(user, _unfinished_races())

    assert group["teams"][0]["can_change"] is True
    assert group["teams"][0]["action_label"] == "Редактировать команду"


@pytest.mark.django_db
def test_home_panel_includes_a_team_beyond_the_upcoming_slice(
    client, django_user_model
):
    user = django_user_model.objects.create_user(username="slice-owner")
    for index in range(4):
        create_owned_race(f"home-slice-{index}", days=index + 2)
    last = create_owned_race("home-slice-last", days=40)
    create_owned_team(user, create_owned_category(last), teamname="Дальняя")
    client.force_login(user)

    response = client.get(reverse("index"))

    assert len(response.context["upcoming_races"]) == 3
    assert last not in list(response.context["upcoming_races"])
    groups = response.context["owned_team_groups"]
    assert [group["race"] for group in groups] == [last]


@pytest.mark.django_db
def test_home_panel_includes_a_team_in_the_featured_race(client, django_user_model):
    user = django_user_model.objects.create_user(username="home-owner")
    race = create_owned_race("home-featured", reg_status=RegStatus.OPEN)
    create_owned_team(user, create_owned_category(race), teamname="Спотлайтовая")
    client.force_login(user)

    response = client.get(reverse("index"))

    assert response.context["featured_race"] == race
    assert list(response.context["upcoming_races"]) == []
    groups = response.context["owned_team_groups"]
    assert [group["race"] for group in groups] == [race]
    assert groups[0]["teams"][0]["name"] == "Спотлайтовая"


@pytest.mark.django_db
def test_home_panel_skips_past_races(client, django_user_model):
    user = django_user_model.objects.create_user(username="past-owner")
    today = timezone.localdate()
    race = create_owned_race(
        "home-past",
        date=today - timedelta(days=10),
        date_end=today - timedelta(days=9),
    )
    create_owned_team(user, create_owned_category(race), teamname="Прошлая")
    client.force_login(user)

    response = client.get(reverse("index"))

    assert response.context["owned_team_groups"] == []


@pytest.mark.django_db
def test_home_panel_skips_unpublished_races(client, django_user_model):
    user = django_user_model.objects.create_user(username="draft-owner")
    race = create_owned_race("home-draft", is_published=False)
    create_owned_team(user, create_owned_category(race), teamname="Черновая")
    client.force_login(user)

    response = client.get(reverse("index"))

    assert response.context["owned_team_groups"] == []


@pytest.mark.django_db
def test_home_panel_is_empty_for_anonymous_visitors(client, django_user_model):
    user = django_user_model.objects.create_user(username="anon-owner")
    race = create_owned_race("home-anon")
    create_owned_team(user, create_owned_category(race), teamname="Чужая")

    response = client.get(reverse("index"))

    assert response.context["owned_team_groups"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("paid_people", [0, 1, 2.5, 3, 4])
def test_home_panel_renders_race_heading_and_team_row(
    client, django_user_model, paid_people
):
    user = django_user_model.objects.create_user(username="render-owner")
    race = create_owned_race(
        "home-render", name="Кольцо 24: весна", is_teams_editable=True
    )
    team = create_owned_team(
        user,
        create_owned_category(race),
        teamname="Лесные коты",
        start_number="18",
        paid_people=paid_people,
    )
    client.force_login(user)

    html = client.get(reverse("index")).content.decode()
    panel = _panel(html)

    assert "Личный кабинет" in panel
    assert "Лесные коты" in panel
    assert "Кольцо 24: весна" in panel
    assert reverse("race", args=[race.slug]) in panel
    assert reverse("edit_team", args=[team.id]) in panel
    assert "Редактировать команду" in panel
    assert '<div class="my-teams__number">18</div>' in panel
    assert "3 участника" in panel
    assert ("3 участника (не оплачено)" in panel) == (paid_people == 0)
    assert ("(оплачено 2,5 из 3)" in panel) == (paid_people == 2.5)
    assert ("(оплачено 1 из 3)" in panel) == (paid_people == 1)
    # The panel sits between the spotlight and the main community content.
    assert html.index('class="my-teams"') < html.index("community-content")


@pytest.mark.django_db
def test_home_panel_renders_numberless_team_and_date_range(client, django_user_model):
    user = django_user_model.objects.create_user(username="range-owner")
    today = timezone.localdate()
    race = create_owned_race(
        "home-range",
        date=today + timedelta(days=5),
        date_end=today + timedelta(days=6),
    )
    create_owned_team(
        user, create_owned_category(race), teamname="Безномерные", start_number=""
    )
    client.force_login(user)

    panel = _panel(client.get(reverse("index")).content.decode())

    number = _extract(
        r'<div class="my-teams__number">(.*?)</div>', panel, "team number cell"
    )
    assert number.strip() == "—"
    dates = _extract(
        r'<span class="my-teams__race-date">(.*?)</span>', panel, "race date"
    )
    assert "–" in dates


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("ucount", "expected"),
    [(1, "1 участник"), (3, "3 участника"), (5, "5 участников")],
)
def test_home_panel_pluralizes_participants(
    client, django_user_model, ucount, expected
):
    user = django_user_model.objects.create_user(username="plural-owner")
    race = create_owned_race("home-plural")
    create_owned_team(
        user,
        create_owned_category(race),
        teamname="Считалочка",
        ucount=ucount,
        paid_people=ucount,
    )
    client.force_login(user)

    panel = _panel(client.get(reverse("index")).content.decode())

    assert f">{expected}</span>" in panel


@pytest.mark.django_db
@pytest.mark.parametrize("login", [False, True])
def test_home_panel_is_absent_without_owned_teams(client, django_user_model, login):
    owner = django_user_model.objects.create_user(username="panel-owner")
    race = create_owned_race("home-absent")
    create_owned_team(owner, create_owned_category(race), teamname="Чужая")
    if login:
        client.force_login(django_user_model.objects.create_user(username="empty"))

    html = client.get(reverse("index")).content.decode()

    assert "Личный кабинет" not in html
    assert "Чужая" not in html


@pytest.mark.django_db
def test_home_panel_renders_view_only_team(client, django_user_model):
    user = django_user_model.objects.create_user(username="locked-render")
    race = create_owned_race("home-locked", is_teams_editable=False)
    create_owned_team(user, create_owned_category(race), teamname="Закрытая")
    client.force_login(user)

    panel = _panel(client.get(reverse("index")).content.decode())

    assert "Посмотреть команду" in panel
    assert "Редактирование закрыто" in panel
    assert "Редактировать команду" not in panel


@pytest.mark.django_db
def test_home_panel_meta_has_no_dangling_separator_without_city(
    client, django_user_model
):
    user = django_user_model.objects.create_user(username="cityless")
    race = create_owned_race("home-cityless")
    create_owned_team(
        user, create_owned_category(race), teamname="Безгородные", city=""
    )
    client.force_login(user)

    panel = _panel(client.get(reverse("index")).content.decode())
    meta = _extract(r'<div class="my-teams__meta">(.*?)</div>', panel, "team meta")

    assert meta.count("·") == 1
    assert "12ч" in meta
    assert "3 участника" in meta
