# Страница «Старты» для организатора

## Overview
- Организаторская страница `race/<slug>/starts/`, по которой видно, как идут старты гонки в реальном времени.
- Старты растянуты на несколько часов. Нужно видеть темп («стартовало X из Y», график) и главное — кого ещё ждём.
- Страница — тонкая оболочка на `base-2.html` плюс JSON-эндпоинт, который JS опрашивает раз в 20 с. Тот же подход, что у «Карты гонки».

## Context (from discovery)
- Источник данных — **только** `Team.start_time` (BigInt, мс, `0` = не стартовала). Его пишет `/app/race/<id>/marks/` по подтверждённой NFC-отметке КП типа `start` (`src/apps/mobile/views.py`) или админ руками. Это те же данные, что идут в протокол (`src/apps/race/results.py`).
- Старые `TeamStartLog` больше не пополняются: эндпоинты удалены в #285.
- Доступ: `src/apps/race/views.py:_load_race_for_admin` (аноним → `login?next=`, без `can_edit_race` → 403). Используется в чек-листе, платежах, списке команд.
- Набор команд и сортировка как в `RaceChecklistView`: `Team.objects.filter(category2__race=race, paid_people__gt=0)` (`TeamManager` отбрасывает удалённые), `sorted(..., key=start_number_key)` из `src/apps/race/teams_admin.py`, имя через `_team_display_name` из `views.py` (нужен `select_related("owner", "category2")`).
- Опрос и config-остров — образец `src/templates/race/map.html` + `src/static/js/race_map.js` (`#raceMapConfig`).
- URL-ы гонки подключены в `src/website/urls.py` (там же `race_checklist`, `race_map`).
- Кнопки админа — `src/templates/race/race_page.html` (~строка 95, рядом с «Карта гонки»).
- `Category`: поля `code`, `short_name`, `name`, `order`.
- Внешние ресурсы запрещены (LAN-режим): график рисуем inline SVG руками, без библиотек.

## Development Approach
- **testing approach**: Regular (сначала код, потом тесты в той же задаче)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest src/apps/race/test_starts.py --reuse-db`
- `make format` и `make lint` перед коммитом

## Testing Strategy
- **unit tests**: pytest-функции с `@pytest.mark.django_db` в новом `src/apps/race/test_starts.py` (`tests.py` уже ~8900 строк). Фабрики импортируются, а не копируются: `from apps.race.tests import _make_race, _make_category, _make_team`.
- **e2e**: в проекте нет JS-тестов и e2e. JS проверяется руками (см. Post-Completion).

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- Два view в `src/apps/race/views.py`:
  - `RaceStartsView` (`race_starts`, `race/<slug>/starts/`) — рендерит `race/starts.html` с config-островом `#raceStartsConfig` = `{"dataUrl": ...}`.
  - `RaceStartsDataView` (`race_starts_data`, `race/<slug>/starts/data/`) — JSON.
- Оба закрыты `_load_race_for_admin`.
- Вся логика экрана (счётчики, фильтр по категории, график, списки, подсветка новых) — в `starts.js` над одним JSON. Сервер только отдаёт строки и форматирует время.
- Решения:
  - Знаменатель — оплаченные команды (`paid_people > 0`). DNS-отметки нет.
  - Время форматирует сервер (`timezone.localtime`), как в `finance.py`: никакой работы с часовыми поясами в браузере.
  - `server_time_ms` в ответе — «сейчас» для «последний старт N мин назад», темпа и правого края графика. Часы ноутбука могут врать.
  - Без ETag: ответ — пара килобайт.

## Technical Details

JSON `race_starts_data`:
```json
{
  "server_time_ms": 1791460000000,
  "categories": [{"id": 3, "code": "6Ч", "name": "6 часов"}],
  "teams": [
    {"id": 41, "start_number": "12", "name": "…", "category_id": 3,
     "paid_people": 4, "start_time_ms": 1791451234000, "start_time": "09:14:05"}
  ],
  "server_time": "09:20:41"
}
```
- `categories`: категории гонки, `order_by("order", "id")`. Неактивные тоже: у них могут быть оплаченные команды.
- `teams`: порядок `start_number_key`. `start_time <= 0` → `start_time_ms: null`, `start_time: null` (как `app_data.format_ms` и `_gpx_time`: неположительное = не задано). Иначе мс как есть и `timezone.localtime(datetime.fromtimestamp(ms/1000, tz=UTC)).strftime("%H:%M:%S")`. Неформатируемое значение (OverflowError/OSError/ValueError) → **оба** поля `null`: команда попадает в «Ждём», а мусорное время не растягивает график и темп.
- Хелпер форматирования — новый, с комментарием, почему не `app_data.format_ms`: тот берёт часовой пояс процесса (`astimezone()`), а не `TIME_ZONE`, и в UTC-контейнере даст неверные часы.
- `paid_people` — `FloatField`; отдаём `int(team.paid_people)`, тест фиксирует тип.
- Ответ через `JsonResponse`.

Экран (`starts.js`):
- Плитки: «Стартовало X из Y (P%)», «Ждём N», «Последний старт N мин назад» (пересчитывается на каждом опросе от `server_time_ms`; ограничено снизу нулём — часы телефона могут спешить), «Темп: K команд за 15 мин».
- Строка категорий `код X/Y` (категории с Y = 0 не показываются); клик — фильтр для всего ниже, повторный клик или «Все» — снять.
- График: накопительная ступенчатая линия, inline SVG; X от первого старта до `max(последний старт, server_time_ms)`; пунктир на уровне Y; подписи оси X — `start_time` первого старта (`HH:MM`) и `server_time`. Нет стартов → «Стартов пока нет».
- «Ждём»: номер, название, категория, людей; поле поиска по номеру и названию.
- «Стартовали»: новые сверху; время, номер, название, категория; строки, которых не было в прошлом опросе, подсвечиваются на несколько секунд (не при первой загрузке).
- Опрос 20 с; фильтр, поиск и прокрутка сохраняются. Ошибка сети/5xx → заметка «нет связи с сервером, данные на HH:MM:SS», старые данные остаются. Ответ не-JSON, 403, 404 или редирект на логин (`response.redirected`) → опрос останавливается, «Сессия истекла или гонка недоступна — перезагрузите страницу».
- Весь пользовательский текст вставляется через `textContent`, не `innerHTML` (названия команд вводят участники).

## What Goes Where
- **Implementation Steps**: код, тесты, CLAUDE.md.
- **Post-Completion**: ручная проверка в браузере.

## Implementation Steps

### Task 1: JSON-эндпоинт `race_starts_data`

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/apps/race/test_starts.py`

- [x] добавить `RaceStartsDataView` в `src/apps/race/views.py`: гейт `_load_race_for_admin`, queryset как в `RaceChecklistView`, сортировка `start_number_key`, имя `_team_display_name`
- [x] сформировать JSON из Technical Details; `server_time_ms` и `server_time` (`HH:MM:SS`, локальное) из `timezone.now()`; хелпер форматирования с комментарием про `format_ms`
- [x] зарегистрировать `race/<slug:race_slug>/starts/data/` как `race_starts_data` в `src/website/urls.py` рядом с `race_map`
- [x] тесты доступа: аноним → редирект на `login` с `next`; пользователь без роли → 403; `RaceAdmin` MODERATOR → 403 (правило `can_edit_race`); `RaceAdmin` ADMIN → 200; суперюзер без `RaceAdmin` → 403
- [x] тесты содержимого: оплаченная команда есть; `paid_people=0`, удалённая (`is_deleted=True`) и команда другой гонки — нет; `start_time=0` и отрицательное → оба поля `null`; `start_time=2**62` (overflow) → оба поля `null`; `paid_people` — `int`; известный момент → те же мс и `HH:MM:SS` в `TIME_ZONE` проекта; порядок `"9"` раньше `"10"`, нечисловой номер в конце; ключи `server_time_ms`/`server_time`/`categories` на месте, категории только этой гонки
- [x] `uv run pytest src/apps/race/test_starts.py --reuse-db` — зелёный

### Task 2: страница `race_starts` и кнопка на странице гонки

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/starts.html`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/test_starts.py`

- [x] добавить `RaceStartsView`: гейт `_load_race_for_admin`, контекст `race` + `"starts_config": _safe_json({"dataUrl": reverse("race_starts_data", ...)})`; в шаблоне руками `<script id="raceStartsConfig" type="application/json">{{ starts_config }}</script>` (как `map.html:63`)
- [x] зарегистрировать `race/<slug:race_slug>/starts/` как `race_starts`
- [x] шаблон `starts.html` на `base-2.html`: обёртка `.race-starts`, `extra_head` с `starts.css`, каркас (плитки, строка категорий, контейнер графика, две колонки, место для статуса связи), `starts.js` с `defer`; ссылка «← к гонке»
- [x] кнопка «Старты» в админском блоке `race_page.html` рядом с «Карта гонки»
- [x] тесты: доступ к странице (аноним → login, без роли → 403, MODERATOR → 403, суперюзер без `RaceAdmin` → 403, ADMIN → 200); в HTML есть `raceStartsConfig` с URL данных; кнопка «Старты» видна админу и не видна обычному пользователю
- [x] прогнать тесты — зелёные

### Task 3: стили `starts.css`

**Files:**
- Create: `src/static/css/starts.css`

- [x] стили под `.race-starts` (без голого `.page`): плитки, чипы категорий (активный выделен), блок графика, две колонки, на ≤720 px одна колонка; подсветка новой строки (CSS-анимация фона); заметка о связи
- [x] проверить, что Django-тесты по-прежнему зелёные (CSS не тестируется)

### Task 4: `starts.js` — опрос, счётчики, списки

**Files:**
- Create: `src/static/js/starts.js`

- [x] читать `#raceStartsConfig`; `fetch(dataUrl, {credentials: "same-origin", headers: {Accept: "application/json"}})` сразу и раз в 20 с
- [x] обработка ошибок: сеть/5xx → заметка «нет связи, данные на …», данные остаются; 403, 404, `response.redirected` или не-JSON → стоп опроса и сообщение
- [x] состояние: выбранная категория, строка поиска, множество id стартовавших с прошлого опроса (для подсветки)
- [x] рендер плиток (X из Y, %, ждём, темп за 15 мин), «последний старт N мин назад» от `server_time_ms`, не меньше 0
- [x] рендер строки категорий с фильтром; списков «Ждём» (поиск по номеру/названию) и «Стартовали» (новые сверху, подсветка новых); только `textContent`; перерисовка без сброса прокрутки колонок
- [ ] проверка руками в браузере на тестовой гонке (тестов JS в проекте нет)

### Task 5: график в `starts.js`

**Files:**
- Modify: `src/static/js/starts.js`

- [x] накопительная ступенчатая линия inline SVG по отфильтрованным стартам: X от первого старта до `max(последний старт, server_time_ms)`, Y от 0 до числа ожидаемых, пунктир на уровне Y
- [x] подписи осей: `start_time` первого старта и `server_time`, значения 0 и Y; цвета из CSS-переменных темы
- [x] пустое состояние «Стартов пока нет»; один старт — линия не ломается (ширина интервала 0 → не делить на ноль)
- [x] перерисовка при опросе и смене фильтра
- [ ] проверка руками

### Task 6: Verify acceptance criteria
- [ ] все пункты Overview и Technical Details реализованы
- [ ] крайние случаи: нет оплаченных команд, никто не стартовал, все стартовали, команда без названия, нечисловые номера, мусорный `start_time`
- [x] `uv run pytest` — весь набор зелёный
- [x] `make lint` — чисто
- [x] нет внешних ресурсов на странице (CDN, шрифты)

### Task 7: [Final] Update documentation
- [x] абзац в `CLAUDE.md`, раздел `apps.race`: `RaceStartsView`/`RaceStartsDataView`, URL-ы, источник только `Team.start_time`, знаменатель `paid_people > 0`, время форматирует сервер, `server_time_ms` как «сейчас», опрос 20 с, гейт `_load_race_for_admin`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- тестовая гонка с несколькими категориями, часть команд со `start_time`; открыть страницу, в `/admin/` проставить старт ещё одной команде — через ≤20 с она уходит из «Ждём», появляется в ленте с подсветкой, график и счётчики растут
- фильтр по категории и поиск переживают опрос
- остановить сервер — появляется «нет связи», данные остаются; запустить — заметка пропадает
- выйти из аккаунта в другой вкладке — опрос останавливается с «перезайдите»
- ширина телефона: одна колонка, нет горизонтальной прокрутки

**Вне объёма** (сознательно): судейские сканы `JudgeScan`, отметка DNS, финиши.
