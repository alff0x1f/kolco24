# Страница «Финиш» для организатора

## Overview
- Организаторская страница `race/<slug>/finishes/`, по которой видно, как идёт финиш: кто финишировал, кто на дистанции, кто опаздывает к КВ.
- Главное новое — **прогноз прихода людей по часам** для кухни: «16:00–17:00 — около 25 человек». Кухня заходит под учёткой админа гонки.
- Старты идут волной несколько часов, поэтому у каждой команды свой КВ: `start_time + control_time`. Обычно все приходят до КВ, большинство — в последний час.
- Устроено как «Старты» (#287): тонкая страница на `base-2.html` плюс JSON-эндпоинт, JS опрашивает его раз в 20 с. Отличие: прогноз считает сервер, чтобы его можно было проверить тестами.

## Context (from discovery)
- Образец — страница «Старты», commit a3165bc: `RaceStartsView`/`RaceStartsDataView` в `src/apps/race/views.py` (~строка 1735), `src/templates/race/starts.html`, `src/static/js/starts.js`, `src/static/css/starts.css`, `src/apps/race/test_starts.py`, URL-ы в `src/website/urls.py`, кнопка в `src/templates/race/race_page.html`.
- `Team.start_time` / `Team.finish_time` — BigInt, мс, `0` = не задано. Оба пишет `/app/race/<id>/marks/` по подтверждённой NFC-отметке КП типа `start`/`finish`, или админ руками. Те же поля читает протокол (`src/apps/race/results.py`).
- КВ — `Category.control_time` (`IntegerField`, минуты, `0` = не задано), `src/website/models/race.py:286`.
- `_start_clock(ms)` в `views.py` форматирует мс в локальное `HH:MM:SS` по `TIME_ZONE` и отдаёт `None` на неположительное или мусорное значение. Годится и для финиша.
- Доступ: `_load_race_for_admin` (аноним → `login?next=`, без `can_edit_race` → 403).
- Набор и порядок команд: `Team.objects.filter(category2__race=race, paid_people__gt=0)`, `sorted(..., key=start_number_key)`, имя `_team_display_name` (`select_related("owner", "category2")`).
- Внешние ресурсы запрещены (LAN-режим): полосы и всё прочее рисуем сами, без библиотек.

## Development Approach
- **testing approach**: Regular (сначала код, потом тесты в той же задаче)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest src/apps/race/test_finishes.py --reuse-db`
- `make format` и `make lint` перед коммитом

## Testing Strategy
- **unit tests**: pytest-функции с `@pytest.mark.django_db` (где нужна БД) в новом `src/apps/race/test_finishes.py`. Фабрики импортируются: `from apps.race.tests import _make_race, _make_category, _make_team`. Модуль прогноза — чистые функции, тесты без БД. `_make_category` не принимает `control_time` — КВ ставим после создания: `cat.control_time = 720; cat.save()`. Хелпер `_admin` импортируем из `apps.race.test_starts`.
- **e2e**: JS-тестов и e2e в проекте нет. JS проверяется руками (см. Post-Completion).

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- Новый модуль `src/apps/race/finish_forecast.py` — чистые функции: состояние команды и раскладка людей по часовым корзинам. Никакого ORM внутри.
- Два view в `src/apps/race/views.py`:
  - `RaceFinishesView` (`race_finishes`, `race/<slug>/finishes/`) — рендерит `race/finishes.html` с config-островом `#raceFinishesConfig` = `{"dataUrl": ...}`.
  - `RaceFinishesDataView` (`race_finishes_data`, `race/<slug>/finishes/data/`) — JSON: команды с состоянием и готовый прогноз.
- Оба закрыты `_load_race_for_admin`. Кнопка «Финиш» на странице гонки рядом со «Стартами».
- JS только рисует: плитки, фильтр категорий, таблица прогноза, списки. Код опроса, ошибок связи и часов сервера **дублируется** из `starts.js`, без общего модуля (решение: страницы живут отдельно).
- Решения:
  - Знаменатель — оплаченные команды (`paid_people > 0`), как в «Стартах».
  - Прогноз в людях (`paid_people`), не в командах: еду готовят на людей.
  - Параметры модели — константы в коде, без настроек в админке.
  - Без ETag.

## Technical Details

### Модель прогноза (`finish_forecast.py`)
Константы в начале модуля, с комментарием «прикидка по опыту: большинство приходит в последний час перед КВ; подкрутить после гонки»:
- `MEAN_BEFORE_DEADLINE_MIN = 40` — среднее время прихода: КВ − 40 мин.
- `SIGMA_MIN = 30` — разброс.
- `OVERDUE_GRACE_MIN = 60` — сколько опоздания ещё считаем «вот-вот придёт».

Для команды на дистанции (КВ `C` мин, идёт уже `e` мин, `e < C`):
- `e = max(e, 0)`: старт «в будущем» (опечатка, спешащие часы телефона) не даёт массы до старта;
- время на дистанции `D ~ N(μ = C − 40, σ = 30)`, известно `e < D ≤ C`;
- `P(D ∈ [a, b]) = (Φ(b) − Φ(a)) / (Φ(C) − Φ(e))`, где `a, b` обрезаны по `[e, C]`, Φ — CDF `N(μ, σ)` через `math.erf`;
- в корзину добавляется `P × paid_people`; сумма по корзинам равна `paid_people`;
- знаменатель меньше `1e-9` → все люди команды в корзину, где лежит её КВ.

Состояния команды (приоритет сверху вниз):
- `finished` — `finish_time > 0` и форматируется;
- `not_started` — старт не задан или мусорный;
- `no_control` — у категории `control_time <= 0`;
- `overdue` — `now ≥ start + C` (ровно в КВ — уже `overdue`); опоздание `≤ 60` мин (включительно) → все люди в первую корзину; `> 60` мин → вне прогноза, флаг `overdue_long: true`;
- `on_course` — остальные.

Корзины — по часам на часах в `TIME_ZONE`:
- сетка `[now, end]`, где `end = max(floor_hour(now) + 1ч, ceil_hour(самый поздний КВ среди команд в прогнозе))`. Так сетка не пустеет, когда в прогнозе только опаздывающие, и не получает лишний пустой час, когда КВ ровно в `HH:00`;
- первая корзина — от `now` до `floor_hour(now) + 1ч` (неполная; при `now = HH:00:00` — целый час, а не корзина нулевой длины);
- корзины полуоткрытые `(from, to]`: КВ ровно в 16:00 лежит в корзине 15:00–16:00. Это же правило решает, куда класть людей при нулевом знаменателе;
- инвариант: `end ≥` КВ каждой команды на дистанции, поэтому масса каждой команды целиком внутри сетки;
- нет команд в прогнозе → пустой список.
- Метки корзин и КВ — `HH:MM` без даты. Для гонок через полночь это неоднозначно; принимаем, порядок держится на мс.
- Функции принимают aware-`datetime`/мс и сами корзины, чтобы тесты задавали «сейчас» явно.

### JSON `race_finishes_data`
```json
{
  "server_time_ms": 1791460000000,
  "server_time": "15:20:41",
  "categories": [{"id": 3, "code": "12Ч", "name": "12 часов", "control_time": 720}],
  "teams": [
    {"id": 41, "start_number": "12", "name": "…", "category_id": 3, "paid_people": 4,
     "start_time_ms": 1791451234000, "start_time": "09:14:05",
     "finish_time_ms": null, "finish_time": null,
     "deadline_ms": 1791494434000, "deadline": "21:14",
     "state": "on_course", "overdue_long": false}
  ],
  "forecast": {
    "all": [{"from": "15:20", "to": "16:00", "people": 4.2}],
    "3": [{"from": "15:20", "to": "16:00", "people": 1.7}]
  }
}
```
- Мусорное время (`<= 0`, overflow) → оба поля (`*_ms` и строка) `null`, как в «Стартах». Хелпер — существующий `_start_clock`.
- `deadline_ms`/`deadline` — только если есть старт и `control_time > 0`, иначе `null`. `deadline` = `_start_clock(deadline_ms)[:5]`; если `_start_clock` вернул `None` (overflow у самого края дат) — оба поля `null`.
- `forecast` — по всем и по **каждой** категории из `categories` (ключ — `str(id)`) на одной сетке корзин, чтобы фильтр в JS просто брал другой ключ. У категории без людей в прогнозе — нули на общей сетке (или `[]`, если сетка пустая). `people` — `float`, округлённый до 0.1; округляет до целого JS.
- `now` берётся в view **один раз** и передаётся и в `server_time_ms`, и в `finish_forecast`.
- `paid_people` — `int(team.paid_people)`.

### Экран (`finishes.js`)
- Плитки: «Финишировало X из Y», «На дистанции N (M чел.)» (`on_course` + `overdue`), «Опаздывают K» (все `overdue`, подпись «из них давно: L»), «Без КВ: J» (только если J > 0). Учитывают фильтр категории.
- Строка категорий как в «Стартах» (`код X/Y`, Y = 0 не показываем), клик — фильтр.
- Прогноз: таблица «`from–to` | людей (целое) | полоса». Длина полосы — от максимума в таблице. Последняя строка — «Всего ≈ N чел.» (сумма до округления строк), чтобы хвост из 0.3 человека на час не потерялся. Подпись: «Прогноз: в среднем за 40 мин до КВ, разброс 30 мин. Опоздавшие больше чем на час не считаются». Пусто → «На дистанции никого».
- «На дистанции»: номер, название, категория, людей, КВ (`HH:MM`), «осталось» (`ч:мм` от `server_time_ms`; у опаздывающих «+мм»). Сортировка по `deadline_ms`; `overdue` сверху и красным, `overdue_long` — серым с «давно».
- «Финишировали»: новые сверху, время, номер, название, категория; подсветка новых строк (не при первой загрузке).
- Опрос, ошибки сети/5xx, стоп на 403/404/редирект/не-JSON, часы сервера — копия из `starts.js`. Весь пользовательский текст — через `textContent`.

## What Goes Where
- **Implementation Steps**: код, тесты, CLAUDE.md.
- **Post-Completion**: ручная проверка в браузере, подстройка констант после гонки.

## Implementation Steps

### Task 1: модуль прогноза `finish_forecast.py`

**Files:**
- Create: `src/apps/race/finish_forecast.py`
- Create: `src/apps/race/test_finishes.py`

- [ ] константы `MEAN_BEFORE_DEADLINE_MIN`, `SIGMA_MIN`, `OVERDUE_GRACE_MIN` с комментарием, откуда они
- [ ] функция состояния команды (`finished`/`not_started`/`no_control`/`overdue`/`on_course` + `overdue_long`) по мс старта, финиша, КВ в минутах и «сейчас»
- [ ] функция сетки часовых корзин в `TIME_ZONE` от «сейчас» до часа самого позднего КВ (первая неполная)
- [ ] функция раскладки людей одной команды по корзинам (условная нормаль на `[e, C]`, защита от нулевого знаменателя, опоздание ≤ 60 мин → первая корзина)
- [ ] функция сборки прогноза: `all` + по категориям на одной сетке, округление до 0.1
- [ ] тесты раскладки: сумма по корзинам = `paid_people` (несколько разных `e`); команда за минуту до КВ → всё в корзине КВ; КВ 12 ч ровно в `HH:00`, старт только что → в последних двух корзинах `> 0.95 × paid_people`; старт в будущем (`e < 0`) → как `e = 0`, масса только внутри `[0, C]`
- [ ] тесты состояний (параметризованные): `now == deadline` → `overdue`; опоздание 59:59 и 60:00 → первая корзина; 60:01 → вне прогноза и `overdue_long`; `control_time=0` → `no_control`; финиш важнее опоздания; положительный, но неформатируемый `finish_time` → не `finished`
- [ ] тесты сетки: первая корзина неполная (`15:20–16:00`); `now = HH:00:00` → первая корзина целый час; самый поздний КВ ровно в `HH:00` → нет лишнего пустого часа; в прогнозе только опаздывающие ≤ 60 мин → одна корзина с их людьми; пустой ввод → `[]`
- [ ] `uv run pytest src/apps/race/test_finishes.py --reuse-db` — зелёный

### Task 2: JSON-эндпоинт `race_finishes_data`

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/test_finishes.py`

- [ ] `RaceFinishesDataView`: гейт `_load_race_for_admin`, queryset и порядок как в `RaceStartsDataView`, время через `_start_clock`, состояние и прогноз из `finish_forecast`
- [ ] `categories` с `control_time`; `deadline_ms`/`deadline` (`HH:MM`); `server_time_ms`/`server_time`
- [ ] зарегистрировать `race/<slug:race_slug>/finishes/data/` как `race_finishes_data` рядом с `race_starts_data`
- [ ] тесты доступа: аноним → `login` с `next`; без роли → 403; MODERATOR → 403; суперюзер без `RaceAdmin` → 403; ADMIN → 200
- [ ] тесты содержимого: каждое состояние у своей команды; `paid_people=0`, удалённая и чужая команда отсутствуют; мусорный `finish_time` (отрицательный, `2**62`) → `null`; `deadline` = старт + КВ в `TIME_ZONE`; ключи `forecast` — `all` + `str(id)` **каждой** категории, включая категорию без КВ; сумма `forecast["all"]` = сумма людей команд в прогнозе с допуском `0.05 × число корзин`
- [ ] прогнать тесты — зелёные

### Task 3: страница `race_finishes` и кнопка

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/finishes.html`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/test_finishes.py`

- [ ] `RaceFinishesView`: гейт, контекст `race` + `finishes_config` = `_safe_json({"dataUrl": ...})`, остров `#raceFinishesConfig` в шаблоне как в `starts.html`
- [ ] URL `race/<slug:race_slug>/finishes/` → `race_finishes`
- [ ] шаблон на `base-2.html`: обёртка `.race-finishes`, `finishes.css` в `extra_head`, каркас (плитки, категории, прогноз, две колонки, статус связи, часы), `finishes.js` с `defer`, ссылка «← к гонке»
- [ ] кнопка «Финиш» в админском блоке `race_page.html` рядом со «Стартами»
- [ ] тесты: доступ к странице (как в Task 2); в HTML есть `raceFinishesConfig` с URL данных; кнопка видна админу и не видна обычному пользователю
- [ ] прогнать тесты — зелёные

### Task 4: стили `finishes.css`

**Files:**
- Create: `src/static/css/finishes.css`

- [ ] стили под `.race-finishes` по образцу `starts.css` (без голого `.page`): плитки, чипы категорий, таблица прогноза с полосами, две колонки → одна на ≤720 px, красные опаздывающие, серые «давно», подсветка новых строк, заметка о связи
- [ ] Django-тесты по-прежнему зелёные

### Task 5: `finishes.js`

**Files:**
- Create: `src/static/js/finishes.js`

- [ ] скопировать из `starts.js` опрос (последовательный, 20 с), обработку ошибок и часы сервера; сменить id острова и тексты
- [ ] плитки и фильтр категорий с учётом состояний
- [ ] таблица прогноза из `forecast[filter || "all"]`: целые люди, полоса, подпись о модели, пустое состояние
- [ ] списки «На дистанции» (сортировка, «осталось»/«+мм», подсветка опоздания) и «Финишировали» (новые сверху, подсветка новых); только `textContent`; фильтр и прокрутка переживают опрос
- [ ] проверка руками в браузере (JS-тестов в проекте нет)

### Task 6: Verify acceptance criteria
- [ ] все пункты Overview и Technical Details реализованы
- [ ] крайние случаи: никто не стартовал, все финишировали, категория без КВ, команда без названия, мусорные времена, опоздание чуть меньше и чуть больше 60 мин
- [ ] `uv run pytest` — весь набор зелёный
- [ ] `make lint` — чисто
- [ ] нет внешних ресурсов на странице

### Task 7: [Final] Update documentation
- [ ] абзац в `CLAUDE.md`, раздел `apps.race`: `RaceFinishesView`/`RaceFinishesDataView`, URL-ы, источник `Team.start_time`/`finish_time` + `Category.control_time`, модель прогноза и константы в `finish_forecast.py`, правило опаздывающих, JS-опрос дублирован из `starts.js` намеренно
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- тестовая гонка с категориями 6 ч и 12 ч, у категорий заполнен `control_time`, часть команд со стартом; открыть страницу — прогноз собирается к КВ
- в `/admin/` поставить команде `finish_time` — через ≤20 с она в «Финишировали» с подсветкой, прогноз уменьшился
- поставить команде старт, при котором КВ прошёл 30 мин назад — она красная и в первой корзине; 90 мин назад — серая «давно», вне прогноза
- фильтр по категории меняет и плитки, и прогноз
- остановить сервер — «нет связи», данные остаются; ширина телефона — одна колонка

**После гонки**: сравнить прогноз с фактическими финишами и подкрутить `MEAN_BEFORE_DEADLINE_MIN`/`SIGMA_MIN`.

**Вне объёма** (сознательно): настройка параметров в админке, подстройка модели по фактическим финишам, `JudgeScan`, отметка схода.
