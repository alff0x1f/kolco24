# Общая навигация главной, публикаций и соревнований

## Цель

Добавить на общие страницы сайта навигационную панель, идентичную по поведению
табам `Обзор / Команды` на странице гонки. Панель должна переключать пять
самостоятельных разделов:

- `Главная` → `/`
- `Все публикации` → `/posts/`
- `Новости` → `/news/`
- `Статьи` → `/articles/`
- `Соревнования` → `/races/`

`/news/` и `/articles/` являются отдельными canonical-маршрутами, а не
query-фильтрами `/posts/`. Поддержка `?kind=news` и `?kind=article` не нужна:
текущая функциональность ещё не была выпущена в production.

## Зафиксированные решения

- Использовать один общий CSS-компонент табов для race-page и общих страниц.
- Показывать общую панель на `/`, `/posts/`, `/news/`, `/articles/` и `/races/`.
- Не показывать её на `/post/<id>/`; там остаются хлебные крошки.
- Удалить `Публикации / Соревнования` из тёмного navbar, чтобы не дублировать
  новую панель.
- На мобильных устройствах сохранять одну строку с горизонтальной прокруткой.
- Не добавлять миграции и не менять модели данных.

## Подход

Стили существующих `race-tabs` обобщаются в `theme-2.css` под нейтральными
классами `section-tabs`, `section-tabs-inner` и `section-tab`. Race-шаблоны и
новый общий include используют эти классы, поэтому состояние sticky, размеры,
цвета и активная линия остаются единой реализацией.

Разметку не нужно насильно сводить к одному шаблону: у табов гонки динамические
URL и счётчик команд, а у общих табов фиксированный набор разделов. Общим
компонентом здесь является CSS/API классов; это избегает условных веток в одном
перегруженном include и одновременно исключает дублирование стилей.

## Стратегия проверки

- Целевые pytest-тесты в `src/website/test_publications.py`.
- Регрессионные тесты `src/apps/race/tests.py` и `src/website/tests.py`.
- `manage.py check`, Ruff, Black, isort и Flake8.
- Smoke-проверка пяти маршрутов и визуальная проверка desktop/mobile.

## Задачи реализации

### Task 1: Добавить отдельные маршруты новостей и статей

**Файлы:**

- Modify: `src/website/urls.py`
- Modify: `src/website/views/community.py`
- Modify: `src/website/test_publications.py`

- [x] Добавить class attributes в `PublicationListView`, например
  `publication_kind = None` и `section_tab = "posts"`, чтобы один view мог
  обслуживать три самостоятельных маршрута.
- [x] Удалить чтение и валидацию `request.GET["kind"]`.
- [x] Фильтровать queryset только по `publication_kind`, заданному URL route.
- [x] Добавить context-поля для заголовка и описания каталога, чтобы `/posts/`,
  `/news/` и `/articles/` имели соответствующие `h1`, title и вводный текст.
- [x] Добавить URL names `news_list` для `/news/` и `article_list` для
  `/articles/`; оставить `publication_list` для `/posts/`.
- [x] Не добавлять редиректы и специальные обработчики старых `kind` query.
- [x] Добавить тесты `reverse()` и статуса 200 для всех трёх каталогов.
- [x] Добавить тесты, что `/posts/` показывает оба типа, `/news/` только новости,
  а `/articles/` только статьи.

### Task 2: Вынести стили табов в общий компонент

**Файлы:**

- Modify: `src/static/css/theme-2.css`
- Modify: `src/static/css/race.css`
- Modify: `src/templates/race/_race_header.html`

- [x] Перенести правила `.race-tabs*` и `.race-tab*` из `race.css` в
  `theme-2.css`, переименовав их в `.section-tabs*` и `.section-tab*`.
- [x] Сохранить существующее поведение: sticky `top: 57px`, белый
  полупрозрачный фон, нижнюю границу, blur, активную синюю линию и badge.
- [x] Добавить `overflow-x: auto`, `white-space: nowrap` и скрытие визуального
  scrollbar без потери клавиатурной доступности.
- [x] Перенести responsive-высоту таба (`46px`) и интервалы из race media query
  в общий CSS.
- [x] Обновить `_race_header.html` на новые нейтральные классы без изменения URL,
  текста, счётчика команд и `aria-current`.
- [x] Удалить старые `.race-tabs*` правила из `race.css`, проверив через `rg`,
  что дублирования больше нет.
- [x] Запустить существующие тесты race-page после переименования классов.

### Task 3: Создать общий tab-bar для разделов сайта

**Файлы:**

- Create: `src/templates/website/_section_tabs.html`
- Modify: `src/templates/website/home.html`
- Modify: `src/templates/website/publication_list.html`
- Modify: `src/templates/website/race_list.html`

- [x] Создать include с пятью фиксированными ссылками и параметром
  `active_tab`.
- [x] Для активной ссылки добавлять `is-active` и `aria-current="page"`.
- [x] Использовать `aria-label="Разделы сайта"` у общего `<nav>`.
- [x] Подключить include сразу после hero на `/` с `active_tab="home"`.
- [x] Подключить include сразу после заголовка каталога публикаций с активным
  значением из view: `posts`, `news` или `articles`.
- [x] Подключить include сразу после заголовка `/races/` с
  `active_tab="races"`.
- [x] Не подключать include в `publication_detail.html`.
- [x] Убедиться, что порядок панели относительно hero совпадает с race-page:
  header → tabs → content.

### Task 4: Удалить старые фильтры и дублирование в navbar

**Файлы:**

- Modify: `src/templates/website/publication_list.html`
- Modify: `src/templates/website/base-2.html`
- Modify: `src/static/css/community.css`

- [x] Удалить `<nav class="publication-filters">` из каталога публикаций.
- [x] Упростить pagination URL до `?page=N`, без сохранения `kind`.
- [x] Удалить CSS правил `.publication-filters` и их mobile override.
- [x] Удалить пункты `Публикации` и `Соревнования` из `.nav-links` тёмного
  navbar; логотип, авторизация и пользовательское меню оставить без изменений.
- [x] Сохранить ссылки `Публикации` и `Соревнования` в footer: это не navbar и
  там они не создают визуального дублирования.

### Task 5: Покрыть активные состояния и доступность тестами

**Файлы:**

- Modify: `src/website/test_publications.py`
- Modify when required: `src/apps/race/tests.py`

- [x] Параметризованным тестом проверить пять общих маршрутов и соответствующий
  активный tab с `aria-current="page"`.
- [x] Проверить наличие ссылок на все пять самостоятельных URL в панели.
- [x] Проверить отсутствие `?kind=` в отрендеренных каталогах и pagination.
- [x] Проверить, что `/post/<id>/` не содержит общей панели, но сохраняет
  хлебные крошки.
- [x] Проверить, что race-page по-прежнему содержит `Обзор / Команды`, правильный
  active state и счётчик команд.
- [x] Проверить, что в тёмном `.nav-links` больше нет ссылок на публикации и
  соревнования.

### Task 6: Финальная проверка

- [x] Выполнить `uv run python src/manage.py check`.
- [x] Выполнить `uv run pytest src/website/test_publications.py
  src/website/tests.py src/apps/race/tests.py --reuse-db`.
- [x] Выполнить `make lint` либо эквивалентные Ruff, Black, isort и Flake8.
- [x] Проверить HTTP 200 для `/`, `/posts/`, `/news/`, `/articles/`, `/races/`.
- [ ] В браузере проверить desktop и mobile: sticky-позицию, активную линию,
  горизонтальную прокрутку и отсутствие двухэтажных табов.
- [x] Убедиться через `git diff --check`, что patch чистый, а `AGENTS.md` и
  `deploy/kolco24.env` не затронуты.

## Критерии готовности

- Пять разделов доступны по самостоятельным URL и показывают корректные данные.
- Общая панель и race-панель используют один набор CSS-классов.
- На каждом hub-маршруте ровно одна активная вкладка.
- Query-фильтры `kind` отсутствуют в коде интерфейса и view.
- Тёмный navbar не дублирует ссылки общей панели.
- Страница отдельной публикации остаётся сфокусированной на чтении.
- Все целевые и регрессионные тесты проходят.

## После завершения

- Переместить план в `docs/plans/completed/` после реализации и финальной
  проверки.
