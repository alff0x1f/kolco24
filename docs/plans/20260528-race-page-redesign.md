# Race Page Redesign — новый дизайн /race/<slug>/

## Overview

Переносим страницу `/race/<slug>/` на новый дизайн из `scratch/Race Page.html`.
Создаём новое Django-приложение `apps.race` с view, шаблоном и CSS.
Модели (Race, Category, RaceLink, NewsPost) остаются в `website`.

Убираем секции без бэкенда: Обсуждения, Фотогалерея, Оргкомитет, Друзья/Сообщество.

## Context

- **Макет:** `scratch/Race Page.html` — standalone HTML, reference дизайна
- **Текущий view:** `RaceNewsView` в `src/website/views/views_.py:395` — сохраняем, его `get_context` используется `AddNewsPostView.post` (line 115)
- **URL:** `race/<slug:race_slug>/` задан в `src/website/urls.py:77` (`views.RaceNewsView.as_view()`)
- **`is_race_admin`:** функция `src/website/views/views_.py:93`
- **`AddNewsPostView.post` (views_.py:103):** при ошибке валидации рендерит `website/news.html` — нужно обновить, чтобы он тоже рендерил новый шаблон
- **Base template:** `src/templates/website/base-2.html` — Rubik font, `{% block extra_head %}`, `{% block content %}`, `main-container` (только min-height)
- **theme-2.css** уже содержит: `.nav`, `.container`, `.btn`, `.card`, `.pill`, CSS-переменные; `.page { padding: 20px 0 56px }` — конфликт!
- **Race.team_count(), Race.people_count()** — методы модели (не поля), вызываются из шаблона автоматически, но запускают отдельные DB-запросы. Нужно вычислить один раз в view.
- **Category-level team_count** — это *аннотация* на queryset категорий (отдельно от Race.team_count())
- **NewsPost fields:** title, publication_date, content_html, image — нет поля `tag`
- **Шаблоны:** в общей папке `src/templates/` (как у demo app), APP_DIRS=True → тоже работает для app-level templates

## Development Approach

- **testing approach:** Regular (code first, затем тесты и проверка в браузере)
- Завершать каждую задачу полностью до перехода к следующей
- Запускать `uv run python src/manage.py check` после каждой задачи с Python/Django изменениями

## Testing Strategy

- **Unit/integration тесты:** Django `TestCase` / pytest в `src/website/tests.py`
- **Существующий тест:** `test_race_news_view_shows_form_for_admin` (tests.py:358) — должен продолжать проходить (URL name `race` не меняется)
- Полный прогон: `uv run pytest --reuse-db`

## Solution Overview

1. Новый Django app `apps.race` (только view, без моделей и миграций)
2. CSS-файл `race.css` со стилями из макета, **без** дублирования theme-2.css
3. Шаблон `race/race_page.html`, расширяющий `base-2.html`
4. Класс `.race-page` вместо `.page` в race.css (избегаем конфликт с theme-2.css, где `.page { padding: 20px 0 56px }`)
5. URL `race/<slug>/` в `website/urls.py` переключается на `RacePageView`
6. `AddNewsPostView.post` обновляется для рендеринга нового шаблона при ошибке формы

## What Goes Where

**Implementation Steps** — всё реализуется в этом репозитории.
**Post-Completion** — ручная проверка в браузере, визуальное сравнение с макетом.

## Implementation Steps

### Task 1: Создать Django app `apps.race`

**Files:**
- Create: `src/apps/__init__.py`
- Create: `src/apps/race/__init__.py`
- Create: `src/apps/race/apps.py`
- Modify: `src/config/settings.py`

- [x] создать `src/apps/__init__.py` (пустой — обязателен для Python package resolution)
- [x] создать `src/apps/race/__init__.py` (пустой)
- [x] создать `src/apps/race/apps.py`:
  ```python
  from django.apps import AppConfig


  class RaceConfig(AppConfig):
      name = "apps.race"
      label = "race_app"
      verbose_name = "Race page"
  ```
  (`label = "race_app"` уникален, нет конфликтов с существующими apps)
- [x] добавить `"apps.race"` в `INSTALLED_APPS` в `src/config/settings.py`
- [x] проверить: `uv run python src/manage.py check` — без ошибок

### Task 2: Создать `RacePageView`

**Files:**
- Create: `src/apps/race/views.py`

**Важно:** `Race.team_count()` и `Race.people_count()` — методы (каждый вызов = отдельный DB-запрос). Вычислить один раз в view и передать как контекст. Аннотация `team_count` на queryset категорий — это другой объект.

- [x] создать `src/apps/race/views.py`:
  ```python
  from django.db.models import Count, OuterRef, Subquery
  from django.http import Http404
  from django.shortcuts import render
  from django.views import View

  from website.forms import NewsPostForm
  from website.models import Category, NewsPost, Race, RegStatus, Team
  from website.views.views_ import is_race_admin


  class RacePageView(View):
      def get(self, request, race_slug):
          try:
              race = Race.objects.get(slug=race_slug)
          except Race.DoesNotExist:
              raise Http404
          categories = (
              Category.active_objects.filter(race=race)
              .order_by("order", "id")
              .annotate(
                  team_count=Subquery(
                      Team.objects.filter(
                          category2=OuterRef("id"),
                          paid_people__gt=0,
                      )
                      .values("category2")
                      .annotate(count=Count("id"))
                      .values("count")[:1]
                  )
              )
          )
          context = {
              "race": race,
              "categories": categories,
              "links": race.links.order_by("-id"),
              "news_list": NewsPost.objects.filter(race=race)[:10],
              "reg_open": race.reg_status == RegStatus.OPEN,
              # Precompute to avoid multiple DB queries in template
              "race_team_count": race.team_count(),
              "race_people_count": race.people_count(),
          }
          if is_race_admin(request.user, race):
              context["post_form"] = NewsPostForm()
          return render(request, "race/race_page.html", context)
  ```
  Анонимным пользователям login-форма не нужна — в шаблоне будет ссылка на `/login/`.
- [x] проверить импорты: `Team` нужен для аннотации (взять из `website.models`)
- [x] `uv run python src/manage.py check` — без ошибок

### Task 3: Подключить URL и обновить `AddNewsPostView`

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/website/views/views_.py`

**Шаг A — URL:**
- [x] добавить импорт в `src/website/urls.py`: `from apps.race.views import RacePageView`
- [x] изменить строку 77: `path("race/<slug:race_slug>/", views.RaceNewsView.as_view(), ...)` → `path("race/<slug:race_slug>/", RacePageView.as_view(), name="race")`
- [x] создать временную заглушку `src/templates/race/race_page.html` (`{% extends "website/base-2.html" %}{% block content %}OK{% endblock %}`) чтобы URL работал
- [x] проверить: `uv run python src/manage.py check` — без ошибок; `curl -s http://localhost:8080/race/kolco24_2025/` → 200

**Шаг B — `AddNewsPostView.post` (views_.py:115-117):**

При ошибке валидации формы этот view рендерит `website/news.html` через `RaceNewsView.get_context`. Обновим его на новый шаблон:

- [x] в `src/website/views/views_.py` найти `AddNewsPostView.post` (line 103)
- [x] заменить блок ошибки формы (lines 115-117):
  ```python
  # было:
  context = RaceNewsView.get_context(race)
  context["post_form"] = form
  return render(request, "website/news.html", context)

  # стало:
  from apps.race.views import RacePageView as _RacePageView
  context = _RacePageView._build_context(race, request.user)
  context["post_form"] = form
  return render(request, "race/race_page.html", context)
  ```
  **Альтернатива проще:** добавить статический метод `build_context` в `RacePageView` (аналог `RaceNewsView.get_context`), вынести туда логику из `get()`.

  Выбираем: добавить `@staticmethod build_context(race, user=None)` в `RacePageView`:
  ```python
  @staticmethod
  def build_context(race, user=None):
      categories = ...  # та же логика
      return {
          "race": race,
          "categories": categories,
          "links": race.links.order_by("-id"),
          "news_list": NewsPost.objects.filter(race=race)[:10],
          "reg_open": race.reg_status == RegStatus.OPEN,
          "race_team_count": race.team_count(),
          "race_people_count": race.people_count(),
      }
  ```
  Затем в `get()`: `context = self.build_context(race, request.user)`.
  В `AddNewsPostView.post`: `from apps.race.views import RacePageView; context = RacePageView.build_context(race, request.user)`.

- [x] обновить `apps/race/views.py` — вынести логику контекста в `build_context(race, user=None)`
- [x] обновить `AddNewsPostView.post` в `views_.py` — использовать `RacePageView.build_context`
- [x] `uv run python src/manage.py check` — без ошибок

### Task 4: Создать `race.css`

**Files:**
- Create: `src/static/css/race.css`

Взять из `scratch/Race Page.html` только race-специфичные стили.

**Берём из scratch (переименовывая `.page` → `.race-page`):**
- `.cover-banner`, `.cover-image`, `.cover-status`
- `.cover-meta-card`, `.cover-meta`, `.cover-title`, `.cover-actions`
- `.race-page { padding: 0 0 56px }` (top=0 т.к. cover banner вверху, в отличие от theme-2.css `.page`)
- `.grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }`
- `.tabs-card`, `.tabs-head`, `.tab-body`
- `.post`, `.post-meta`, `.post-title`, `.post-body`, `.post-foot`, `.post-image`, `.post-empty`
- `.sidebar`, `.side-head`, `.side-menu`, `.info-list`
- `.kicker`, `.badge` (для счётчика команд в side-menu)
- responsive breakpoints (≤900px grid→1col, ≤600px)

**Для pill-вариантов:** проверить `theme-2.css` на наличие `.pill.success` и `.pill.warn` — если нет, добавить в `race.css`.

**НЕ копируем:** `.nav`, `.nav-*`, `.brand*`, `.container`, `.btn`, `.btn-*`, `.card`, `.pill` (базовый), `:root {}` (CSS-переменные)

- [x] проверить в theme-2.css: наличие `.pill.success`, `.pill.warn` (или аналогов)
- [x] создать `src/static/css/race.css` с указанными стилями
- [x] не создавать `.page` — только `.race-page`
- [x] убедиться, что нет дублирующих class names с theme-2.css

### Task 5: Написать шаблон `race_page.html`

**Files:**
- Create: `src/templates/race/` (директория)
- Create: `src/templates/race/race_page.html` (заменяет заглушку из Task 3)

В шаблоне используем `race_team_count` и `race_people_count` из контекста (не `race.team_count` напрямую).

- [x] написать заголовок шаблона:
  ```html
  {% extends "website/base-2.html" %}
  {% load static %}
  {% block title %}{{ race.name }} · {{ race.date }}{% endblock %}
  {% block extra_head %}
  <link href="{% static 'css/race.css' %}" rel="stylesheet">
  {% endblock %}
  ```
- [x] написать cover banner в `{% block content %}`:
  - `<div class="cover-banner">` → `<div class="cover-image"` с `style="background-image: url('{{ race.header_image }}')"` если `race.header_image` не пустой
  - pill статуса: `upcoming` → серая, `open` → `.pill.success`, `sold_out` → оранжевая
- [x] написать cover-meta-card:
  - `h1`: `{{ race.name }} · {{ race.date }}`
  - `.sub`: `📍 {{ race.place }}`, `{{ race_people_count }} участников`, `{{ race_team_count }} команд`
  - cover-actions:
    - `{% if race.reg_status == 'open' %}` + `{% if user.is_authenticated %}` → кнопка "Добавить команду" → `{% url 'add_team' race.slug %}`
    - `{% if race.reg_status == 'open' %}` + не auth → кнопка "Зарегистрироваться" → `{% url 'register' %}`
    - иначе — кнопки регистрации нет
    - всегда: кнопка "Все команды" (btn-ghost) → `{% url 'all_teams' race.slug %}`
- [x] написать main-колонку:
  - форма нового поста: `{% if post_form %}<form action="{% url 'add_post' race.slug %}" method="post" enctype="multipart/form-data">...{% endif %}`
  - tabs-card с `tabs-head` "Новости" + `<span class="badge">{{ news_list|length }}</span>`
  - `tab-body`: `{% for news in news_list %} ... {% empty %}<p class="post-empty">Новостей пока нет.</p>{% endfor %}`
  - article.post: `.post-meta` с `<time>{{ news.publication_date }}</time>`, `.post-title`, `.post-image` (если `news.image`), `.post-body {{ news.content_html|safe }}`
  - нет поля `tag` у NewsPost — не выводим
- [x] написать sidebar:
  - Race info card: SVG-иконки из макета + место, дата (`{{ race.date }}`), участников (`{{ race_people_count }}`), команд (`{{ race_team_count }}`), взнос (`{{ race.cost }} ₽/уч`)
  - Полезные ссылки card: `{% if links %}{% for link in links %}<li><a href="{{ link.url }}">{{ link.name }}</a></li>{% endfor %}{% endif %}`
  - Teams card: "Все `{{ race_team_count }}`" → `{% url 'all_teams' race.slug %}`; per-category с `category.team_count` → `{% url 'teams2' race.slug category.id %}`; кнопка "Добавить команду" если `reg_open and user.is_authenticated`
- [x] запустить dev server: `uv run python src/manage.py runserver 0:8080`
- [x] проверить в браузере: `/race/kolco24_2025/` — страница рендерится без ошибок

### Task 6: Тесты

**Files:**
- Modify: `src/website/tests.py`

- [x] убедиться, что `test_race_news_view_shows_form_for_admin` (tests.py:358) **проходит без изменений** (URL name `race` сохранён, логика post_form идентична)
- [x] добавить тест на статус 200 и правильный шаблон:
  ```python
  def test_race_page_view_status_200(client):
      race = Race.objects.create(name="T", code="t25", slug="t-2025")
      response = client.get(f"/race/{race.slug}/")
      assert response.status_code == 200
      assert "race/race_page.html" in [t.name for t in response.templates]
  ```
- [x] добавить тест на 404 для несуществующего slug:
  ```python
  def test_race_page_view_404_for_unknown_slug(client):
      response = client.get("/race/nonexistent-2099/")
      assert response.status_code == 404
  ```
- [x] добавить тест на контекст (ключи `categories`, `links`, `news_list`, `reg_open`, `race_team_count`, `race_people_count`):
  ```python
  def test_race_page_view_context_keys(client):
      race = Race.objects.create(name="C", code="c25", slug="c-2025")
      response = client.get(f"/race/{race.slug}/")
      for key in ("categories", "links", "news_list", "reg_open",
                  "race_team_count", "race_people_count"):
          assert key in response.context, f"context missing: {key}"
  ```
- [x] добавить тест: анонимный пользователь не видит `post_form`:
  ```python
  def test_race_page_view_no_post_form_for_anon(client):
      race = Race.objects.create(name="A", code="a25", slug="a-2025")
      response = client.get(f"/race/{race.slug}/")
      assert "post_form" not in response.context
  ```
- [x] запустить: `uv run pytest src/website/tests.py --reuse-db -x`
- [x] запустить полный набор: `uv run pytest --reuse-db`

### Task 7: Финальная проверка и форматирование

- [ ] `make format` + `make lint` — без ошибок
- [ ] визуально сравнить `/race/<slug>/` с макетом `scratch/Race Page.html`
- [ ] проверить все три состояния `reg_status` (upcoming, open, sold_out) — нужные кнопки появляются/исчезают
- [ ] проверить cover banner: с `race.header_image` и без (должен быть градиент-плейсхолдер)
- [ ] проверить на узком viewport (≤600px) — sidebar уходит под main column
- [ ] войти как race admin → форма нового поста видна
- [ ] убедиться что `race.date` рендерит кириллическое название месяца (LANGUAGE_CODE=ru-ru)
- [ ] переместить план: `mv docs/plans/20260528-race-page-redesign.md docs/plans/completed/`

## Post-Completion

**Ручная проверка после деплоя:**
- Войти как race admin → форма поста
- Отправить невалидную форму → страница должна показать новый дизайн (не старый Bootstrap)
- `reg_status=open` + не аутентифицирован → кнопка "Зарегистрироваться"
- `reg_status=sold_out` → никакой кнопки регистрации

**Старый код сохранён:**
- `src/templates/website/news.html` — остаётся (может понадобиться для rollback)
- `RaceNewsView` — остаётся в `website/views/views_.py` (метод `get_context` больше не используется в AddNewsPostView после Task 3B, но view остаётся для безопасности)
