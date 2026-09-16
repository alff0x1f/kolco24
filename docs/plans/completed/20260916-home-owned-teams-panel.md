# Панель «Личный кабинет» на главной странице

## Overview

На странице гонки (`/race/<slug>/`) залогиненный пользователь видит панель «Личный кабинет» со
своими командами этой гонки. На главной странице такой панели нет — чтобы попасть к своей команде,
нужно сначала вспомнить, в какой гонке она зарегистрирована, и открыть страницу гонки.

Задача: показать на главной все команды пользователя во **всех будущих и текущих** соревнованиях
(критерий `date_end >= сегодня`, то есть идущая прямо сейчас гонка тоже попадает),
сгруппированные по гонке. Панель рендерится только если команды есть; аноним её не видит и запроса
команд не вызывает, залогиненный без команд видит пустую секцию (не рендерится), но один запрос
команд всё же выполняется.

Интеграция: новый блок на всю ширину между спотлайтом гонки и основным гридом главной. Страница
гонки не меняется вообще.

## Context (from discovery)

Файлы и компоненты:

- `src/apps/race/views.py:87-113` — `_owned_teams(race, user)`, источник данных панели на странице
  гонки. Читается как образец; **не меняется и не импортируется**.
- `src/templates/race/_owned_teams.html` — существующий шаблон панели. **Не меняется.**
- `src/static/css/race.css:130-206, 485-514` — стили `.owned-*`. **Не меняются.**
- `src/website/views/community.py:16-52` — `get_featured_race()` и `HomeView.get`. Точка изменения.
- `src/templates/website/home.html` — шаблон главной. Точка изменения.
- `src/static/css/community.css` — 865 строк, брейкпоинты 920 / 720 / 620 / 600. Точка изменения.
- `src/website/test_publications.py` — **здесь живут все тесты главной** (`HomeView`):
  `test_home_is_a_real_page_and_shows_only_visible_publications`,
  `test_home_keeps_a_current_race_in_compact_calendar` (утверждает
  `list(response.context["upcoming_races"]) == [current]`), параметризованные тесты `featured_race`.
  Точка изменения. В `src/website/tests.py` тестов главной нет — новые тесты идут **не туда**.

Найденные паттерны:

- Главная уже определяет будущие гонки как `Race.objects.filter(is_published=True,
  date_end__gte=today)` (`community.py:36-39`) — переиспользуем тот же фильтр, чтобы панель и список
  «Соревнования» не расходились в определении «будущего».
- Разметка главной в BEM-стиле: `race-spotlight__inner`, `race-list-card__body`,
  `community-button`. Страница гонки использует другой словарь (`.card`, `.btn`, `.owned-team-row`).
  Новый блок говорит на языке главной.
- `community-button` имеет два модификатора: `--accent` (заливка, светлый фон) и `--outline-light`
  (для тёмной подложки спотлайта). Нейтрального варианта для светлого фона нет — понадобится
  локальный модификатор внутри блока `.my-teams`.
- `ru_plural` — фильтр из `website.templatetags.custom_filters`, возвращает только существительное.
- `apps/race/permissions.py:6-8` — `is_team_editing_open(user, race)`: чистая проверка полей
  (`race.is_teams_editable or user.is_superuser`), запросов не делает, безопасно звать в цикле.
- Тесты проекта — pytest-функции с `@pytest.mark.django_db` и фикстурами `client` /
  `django_user_model`, не `TestCase`.

Зависимости:

- `website.models.Team`, `website.models.Race`.
- `apps.race.permissions.is_team_editing_open` — импорт из `website.views.community`. Модуль
  `apps/race/permissions.py` импортирует только `website.models`, цикла не создаёт (в отличие от
  `apps.race.views`, который в проекте уже тянут отложенным импортом).

## Development Approach

- **testing approach**: Regular (код, затем тесты) — верстка и группировка проще проверяются на
  готовом выводе.
- выполнять задачи по порядку, каждую до конца
- **каждая задача с изменением кода обязана содержать новые/обновлённые тесты**
- **все тесты должны проходить перед началом следующей задачи**
- запускать `uv run pytest --reuse-db src/website/` после каждого изменения — не только
  `tests.py`: задача 2 трогает `upcoming_races`, который защищён тестами в `test_publications.py`
- **обновлять этот файл, если скоуп меняется по ходу**
- перед коммитом — `make format && make lint`
- работать в ветке от `master`, в `master` не коммитить

## Testing Strategy

- **unit/view-тесты**: обязательны в каждой задаче с изменением Python/шаблонов, см. выше. Проверяют
  контекст (`owned_team_groups`) и отрендеренный HTML главной страницы. Место —
  `src/website/test_publications.py`, рядом с существующими тестами `HomeView` (там же доступны
  хелпер `create_publication` и принятый в файле идиом `Race.objects.create`).
- **исключение**: задача 4 — чистый CSS, тестов не имеет и тестовой проверки не требует; выдумывать
  тест на стили не нужно.
- **e2e**: в проекте нет Playwright/Cypress — e2e-тестов не добавляем.
- Проверку числа запросов (`assertNumQueries`) не добавляем: группировка идёт в Python над одним
  queryset, тест на счётчик запросов был бы хрупким без пользы.
- Верстка и адаптив проверяются вручную (см. Post-Completion).

## Progress Tracking

- отмечать выполненное `[x]` сразу
- новые задачи помечать префиксом ➕
- проблемы и блокеры — префиксом ⚠️
- при отклонении от плана обновлять план

## Solution Overview

Выбран **путь дублирования**, а не общей абстракции. Панель на странице гонки и панель на главной
живут отдельно: свой сборщик данных, свой шаблон, свой CSS. Обоснование: страницы используют разные
дизайн-системы (`race.css` против `community.css`, `.btn` против `.community-button`), а вынос общего
партиала потребовал бы добавить в него «режим с заголовком гонки» и связать две независимые страницы
одним файлом стилей. Цена — дублирование ~25 строк Python и ~40 строк разметки; это принято сознательно.

Поток данных:

1. `HomeView.get` строит базовый queryset `future_races` (`is_published=True, date_end__gte=today`,
   порядок `date, pk`). Критерий включает и идущую сейчас гонку — так же, как существующий список
   «Соревнования» (см. `test_home_keeps_a_current_race_in_compact_calendar`).
2. Из него, как и сейчас, получается `upcoming_races` — минус `featured_race`, срез `[:3]`, для
   правой колонки.
3. `owned_teams_by_race(request.user, future_races)` делает **один** запрос по всем будущим гонкам и
   группирует результат в Python (у анонима — ноль запросов).
4. Результат кладётся в контекст как `owned_team_groups` и рендерится новым партиалом.

Ключевое решение: в панель уходит `future_races` **целиком**, а не урезанный `upcoming_races` —
команда пользователя может быть и в спотлайт-гонке (которая из `upcoming_races` исключена), и в
четвёртой по счёту (которая отрезана срезом).

## Technical Details

### Структура данных

`owned_teams_by_race(user, races)` возвращает список групп:

```python
[
    {
        "race": <Race>,
        "teams": [
            {
                "id": int,
                "name": str,           # teamname или фолбэк
                "number": str,         # team.start_number — CharField, НЕ int
                "category": str,       # short_name или name
                "city": str,
                "participants": int,   # team.ucount
                "url": str,            # reverse("edit_team", args=[team.id])
                "action_label": str,   # «Редактировать команду» / «Посмотреть команду»
                "can_change": bool,
            },
            ...
        ],
    },
    ...
]
```

Пустой список, если `user` — `None` или аноним; в этом случае запрос команд не выполняется.

`start_number` — `CharField(max_length=50, default="", blank=True)`
(`src/website/models/models.py:198`), поэтому `order_by("start_number")` сортирует лексикографически
(`"10" < "9"`). Это **намеренно** — ровно так же сортирует панель на странице гонки; «чинить» на
числовую сортировку не нужно.

Проверки «`races` пуст» **не делаем**: `races` — ленивый queryset, и `if not races` выполнил бы
отдельный SELECT всех будущих гонок ради ответа «пусто». `__in` по пустому queryset — один дешёвый
запрос, этого достаточно.

### Запрос

```python
Team.objects.filter(category2__race__in=races, owner=user)
    .select_related("category2", "category2__race")
    .order_by(
        "category2__race__date",
        "category2__race_id",
        "category2__order",
        "start_number",
        "id",
    )
```

`TeamManager` уже исключает `is_deleted`. Группировка — проходом по отсортированному списку с
накоплением текущей группы (порядок групп совпадает с порядком гонок по дате).

`can_change` считается один раз на гонку (`is_team_editing_open(user, race)`), не на команду.

### Фолбэк имени команды

Копируется из `apps/race/views.py:_team_display_name` в `community.py`, но с **изменённой
сигнатурой** — владелец передаётся явно:

```python
def _team_display_name(team, user):
    return team.teamname or f"Без названия {team.id} ({user.last_name} {user.first_name})"
```

Оригинал читает `team.owner.*` и потому тащит `select_related("owner")`. Здесь все команды по
построению принадлежат `user`, поэтому берём поля прямо из него: `owner` в `select_related` не нужен
и обещание «один запрос» не ломается. Слепая копия оригинала дала бы +1 запрос на каждую безымянную
команду.

Импорт `apps.race.views._team_display_name` **не делаем** — `apps.race.views` тянет `website.views`,
в проекте из-за этого уже есть отложенный импорт (см. CLAUDE.md про `AddNewsPostView`).

### Разметка

Новый файл `src/templates/website/_home_owned_teams.html`:

```django
{% load custom_filters %}
{% if owned_team_groups %}
<section class="my-teams" aria-labelledby="my-teams-title">
  <div class="container">
    <div class="my-teams__head">
      <span class="my-teams__kicker">Личный кабинет</span>
      <h2 id="my-teams-title">Ваши команды</h2>
    </div>
    {% for group in owned_team_groups %}
    <div class="my-teams__group">
      <h3 class="my-teams__race">
        <a href="{% url 'race' group.race.slug %}">{{ group.race.name }}</a>
        <span class="my-teams__race-date">{% if group.race.date_end != group.race.date %}{{ group.race.date|date:"j E" }}–{{ group.race.date_end|date:"j E Y" }}{% else %}{{ group.race.date|date:"j E Y" }}{% endif %}</span>
      </h3>
      {% for team in group.teams %}
      <article class="my-teams__row">
        <div class="my-teams__number">{% if team.number %}№ {{ team.number }}{% else %}Без номера{% endif %}</div>
        <div class="my-teams__copy">
          <h4>{{ team.name }}</h4>
          <div class="my-teams__meta">
            <span>{{ team.category }}</span>
            {% if team.city %}<span class="my-teams__sep" aria-hidden="true">·</span><span>{{ team.city }}</span>{% endif %}
            <span class="my-teams__sep" aria-hidden="true">·</span>
            <span>{{ team.participants }} {{ team.participants|ru_plural:"участник,участника,участников" }}</span>
          </div>
          {% if not team.can_change %}<span class="my-teams__locked">Редактирование закрыто</span>{% endif %}
        </div>
        <a class="community-button {% if team.can_change %}community-button--accent{% else %}my-teams__button--ghost{% endif %}"
           href="{{ team.url }}">{{ team.action_label }}</a>
      </article>
      {% endfor %}
    </div>
    {% endfor %}
  </div>
</section>
{% endif %}
```

Заголовок гонки показывается всегда, даже при одной группе — на главной без него непонятно, к какому
старту относится команда. Заголовок секции — всегда во множественном числе. `ru_plural` используется
только для «участник/участника/участников». Проверка `user.is_authenticated` не нужна: у анонима
`owned_team_groups` пуст.

Подключение в `home.html` между спотлайтом и `<main>`:

```django
{% include "website/_race_spotlight.html" with featured_race=featured_race only %}
{% include "website/_home_owned_teams.html" %}
<main class="community-content">
```

(без `only` — партиалу нужен `owned_team_groups` из контекста страницы)

### CSS

В конец `src/static/css/community.css`, префикс `.my-teams`. Новых брейкпоинтов не вводим:

- десктоп: `.my-teams__row` — `display: grid; grid-template-columns: auto minmax(0, 1fr) auto;`
  строки разделены тонкой границей, группы — отступом; `.my-teams__race` мелкий, приглушённый,
  дата справа.
- `@media (max-width: 720px)`: кнопка переносится на вторую строку (`grid-column: 2`).
- `@media (max-width: 600px)`: одна колонка, номер слева, кнопка на всю ширину.
- `.my-teams__button--ghost` — локальный нейтральный вариант кнопки (рамка + прозрачный фон) для
  `can_change = False`; глобальный `--outline-light` не годится, он рассчитан на тёмную подложку
  спотлайта.

### Вне скоупа

- ссылка «мои команды» в шапке сайта
- любые изменения страницы гонки, `race/_owned_teams.html`, `race.css`
- общий партиал или общий CSS-файл для двух панелей
- пустое состояние «вы ещё не зарегистрировали команду» на главной (призыв к регистрации уже есть
  в спотлайте; на несколько гонок он превратился бы в шум)

## What Goes Where

- **Implementation Steps** (`[ ]`): изменения кода, шаблонов, стилей и тестов в этом репозитории
- **Post-Completion** (без чекбоксов): ручная проверка верстки в браузере

## Implementation Steps

### Task 1: Сборщик данных `owned_teams_by_race`

**Files:**
- Modify: `src/website/views/community.py`
- Modify: `src/website/test_publications.py`

- [x] добавить импорты `Team` и `is_team_editing_open` в `src/website/views/community.py`
- [x] добавить приватный `_team_display_name(team, user)` — владелец передаётся **явно**, поля
      берутся из `user`, а не из `team.owner` (иначе +1 запрос на безымянную команду)
- [x] добавить `owned_teams_by_race(user, races)` рядом с `get_featured_race`: ранний выход `[]`
      только для `None`/анонима (проверки «`races` пуст» нет — см. «Technical Details»), один запрос
      с `select_related` и сортировкой из «Technical Details», группировка в Python, `can_change`
      считается один раз на гонку
- [x] написать тесты функции: команда в будущей гонке попадает в группу; поля группы и команды
      заполнены (`name`, `number`, `category`, `city`, `participants`, `url`, `action_label`)
- [x] написать тесты граничных случаев: аноним → `[]`; чужая команда не попадает;
      `is_teams_editable=False` → `can_change` False и «Посмотреть команду»; команды в двух гонках
      дают две группы в порядке дат; безымянная команда получает фолбэк «Без названия N (…)»
- [x] запустить `uv run pytest --reuse-db src/website/` — должно пройти перед задачей 2

### Task 2: Проброс `owned_team_groups` в контекст главной

**Files:**
- Modify: `src/website/views/community.py`
- Modify: `src/website/test_publications.py`

- [x] в `HomeView.get` выделить базовый queryset `future_races` (`is_published=True`,
      `date_end__gte=today`, `order_by("date", "pk")`)
- [x] переписать `upcoming_races` как производный от `future_races` (исключение `featured_race`,
      срез `[:3]`) — поведение правой колонки не меняется
- [x] добавить в контекст `owned_team_groups = owned_teams_by_race(request.user, future_races)`
- [x] написать тест: у пользователя с командой в спотлайт-гонке панель не пустая (спотлайт-гонка
      исключена из `upcoming_races`, но обязана попасть в панель)
- [x] написать тест: команда в прошедшей гонке (`date_end` < сегодня) в `owned_team_groups` не
      попадает
- [x] написать тест: аноним получает пустой `owned_team_groups`
- [x] написать тест: команда в **неопубликованной** гонке (`is_published=False`) в панель не
      попадает — фиксируем намеренное поведение фильтра
- [x] запустить `uv run pytest --reuse-db src/website/` — существующие тесты
      `test_home_keeps_a_current_race_in_compact_calendar` и параметризованные `featured_race`
      обязаны остаться зелёными (правая колонка не должна измениться)

### Task 3: Шаблон панели на главной

**Files:**
- Create: `src/templates/website/_home_owned_teams.html`
- Modify: `src/templates/website/home.html`
- Modify: `src/website/test_publications.py`

- [x] создать `_home_owned_teams.html` по разметке из «Technical Details» (`{% load custom_filters %}`,
      обёртка `{% if owned_team_groups %}`, группы с заголовком гонки, строки команд, кнопка
      `community-button`)
- [x] подключить партиал в `home.html` между `_race_spotlight.html` и `<main class="community-content">`
- [x] написать тест рендера: в HTML главной есть «Личный кабинет», название команды, название гонки
- [x] написать тесты отрицательных случаев: у анонима и у залогиненного без команд в HTML нет
      «Личный кабинет»
- [x] написать тест: при `is_teams_editable=False` в HTML есть «Посмотреть команду» и
      «Редактирование закрыто»
- [x] написать тест: у команды с пустым `city` в мета-строке нет висящего разделителя `·`
- [x] запустить `uv run pytest --reuse-db src/website/` — должно пройти перед задачей 4

### Task 4: Стили панели

Чистый CSS — тестов не имеет и не требует (см. исключение в «Testing Strategy»).

**Files:**
- Modify: `src/static/css/community.css`

- [x] добавить в конец файла блок `.my-teams` (секция, `__head`, `__kicker`, `__group`, `__race`,
      `__race-date`) — ➕ [decision] блок вставлен **перед** существующими `@media`, а не в самый конец
      файла: иначе базовые правила перебили бы адаптивные (одинаковая специфичность, побеждает
      последнее); медиазапросы и так стоят в конце файла
- [x] добавить `.my-teams__row` — grid `auto minmax(0, 1fr) auto`, разделители строк, стили
      `__number`, `__copy`, `__meta`, `__sep`, `__locked`
- [x] добавить `.my-teams__button--ghost` — нейтральная кнопка для светлого фона
- [x] добавить правила в существующий `@media (max-width: 720px)`: кнопка на вторую строку
- [x] добавить правила в существующий `@media (max-width: 600px)`: одна колонка, кнопка на всю ширину
- [x] проверить, что новых брейкпоинтов не добавлено и имена классов не пересекаются с существующими
      (`grep -n "my-teams" src/static/css/community.css`)

### Task 5: Verify acceptance criteria

- [x] проверить, что все требования из Overview реализованы
- [x] проверить, что `src/apps/race/views.py`, `src/templates/race/_owned_teams.html` и
      `src/static/css/race.css` не изменены (`git diff --stat`)
- [x] прогнать весь набор тестов: `uv run pytest` (включая `src/apps/race/tests.py` — панель
      страницы гонки должна остаться нетронутой) — 1204 passed
- [x] прогнать `make format && make lint` — чисто, правок не потребовалось
- [x] убедиться, что работа идёт в ветке от `master`, а не в `master` — ветка
      `home-owned-teams-panel`

### Task 6: [Final] Update documentation

- [x] обновить CLAUDE.md: в описании главной упомянуть панель «Личный кабинет»
      (`owned_team_groups`, `_home_owned_teams.html`) и явно зафиксировать, что она **дублирует**
      панель страницы гонки — общего партиала/CSS нет намеренно — добавлен абзац
      **Home page owned-teams panel** перед «Custom error pages»
- [x] README.md правок не требует — проверено: README.md не описывает главную страницу
      (нет упоминаний home/HomeView), правок не нужно
- [x] перенести этот план в `docs/plans/completed/` (moved by orchestrator at completion)

## Post-Completion

*Требует ручных действий, чекбоксов нет*

**Ручная проверка:**

- открыть главную под пользователем с командами в двух будущих гонках — проверить порядок групп,
  заголовки гонок, кнопки
- проверить верстку на ширинах ~1200 / 720 / 375 px: перенос кнопки, кнопка на всю ширину, отсутствие
  горизонтального скролла
- проверить главную под анонимом и под пользователем без команд — панели нет, отступы между спотлайтом
  и гридом не поехали
- проверить, что страница гонки визуально не изменилась
