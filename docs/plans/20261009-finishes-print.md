# Печать прихода людей для кухни и столбец «пришло»

## Overview
- Кухне нужен бумажный лист: сколько людей пришло в каждый прошедший час и сколько ожидается в следующие. Факт нужен, чтобы кухня видела объём: «два часа назад пришло n человек, ушло столько-то еды».
- Новая печатная страница `race/<slug>/finishes/print/`. Это отдельный документ без базового шаблона, как «Лист выдачи».
- Прогноз и факт сводятся в **одну шкалу часов**: прошедшие часы — «пришло», будущие — «ожидается», текущий час — оба числа. Эта же шкала заменяет таблицу прогноза на живой странице «Финиш».

## Context (from discovery)
- Ветка `race-finishes-page`, страница «Финиш» уже закоммичена (d7cf59a). План страницы — `docs/plans/20261008-race-finishes-page.md`.
- `src/apps/race/finish_forecast.py`: `team_state`, `deadline_ms`, `hour_buckets`, `spread_team`, `build_forecast`. Корзины `(from, to]`, первая — от «сейчас» до следующего часа.
- `src/apps/race/views.py`:
  - `RaceFinishesView` и `RaceFinishesDataView`. Второй собирает строки команд и `forecast_teams` прямо в `get`.
  - `_start_clock` и `_load_race_for_admin`.
  - `_selected_category(request, race)` — разбор `?category=`, его использует `RaceChecklistView`. Неизвестный id → вся гонка.
- Печатный образец: `src/templates/race/checklist.html` — standalone-документ со `{% static %}` CSS и панелью `.toolbar` (выбор категории, «← гонка»), CSS `checklist.css`.
- Живая страница: `src/templates/race/finishes.html`, `src/static/js/finishes.js` (`renderForecast`), `src/static/css/finishes.css`.
- Тесты: `src/apps/race/test_finishes.py` (хелперы `_at`, `_team`, `_category`, `_data`).

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
- **unit tests**: pytest-функции в `src/apps/race/test_finishes.py`. `build_timeline` — чистая функция, тесты без БД. View — `@pytest.mark.django_db`.
- **e2e**: JS-тестов и e2e в проекте нет. Живая страница и печать проверяются руками (см. Post-Completion).

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- `build_timeline(teams, category_ids, now_ms)` в `finish_forecast.py` строит общую шкалу. Ядро прогноза выносится во внутреннюю `_forecast_totals(teams, category_ids, now_ms) -> (buckets_ms, {key: [float]})` — неокруглённые значения и границы в мс. `build_forecast` только подписывает и округляет его результат, `build_timeline` берёт те же неокруглённые значения и сам считает факт. Сопоставление по строковым меткам запрещено: метки повторяются через полночь.
- JSON-ключ `forecast` переименовывается в `timeline`: формат строк меняется, старое имя путало бы.
- Сборка команд из `RaceFinishesDataView.get` выносится в хелпер `_finish_rows(race, now_ms)` (без параметра категории), который возвращает `(rows, forecast_teams)`. Data view и печатный view берут его оба: это один файл, и дублировать 40 строк незачем.
- Печать с `?category=X` показывает `build_timeline(все команды)["X"]` — ту же сетку и те же числа, что живая страница с этим фильтром. Категория фильтрует только сноску.
- `RaceFinishesPrintView` (`race_finishes_print`) рендерит таблицу на сервере, без JS.
- На живой странице — те же столбцы и кнопка «Печать для кухни».

## Technical Details

### `build_timeline`
- Вход: те же dicts команд, что у `build_forecast`, плюс `finish_ms` (мс или `None`).
- Сетка часов в `TIME_ZONE`:
  - начало — `max(floor_hour(самый ранний finish_ms), floor_hour(now) − 24ч)`, а если финишей нет — `floor_hour(now)`. Финиш позже `now` в начале сетки не участвует;
  - нижняя граница 24 ч нужна, потому что `finish_time` правится руками в `/admin/`: опечатка «секунды вместо мс» (1970 год) или чужой день иначе дали бы сотни тысяч строк на каждом опросе. Финиши раньше границы складываются в одну строку «раньше» в начале шкалы: `{"from": null, "to": "<граница>", "arrived": N, "expected": null, "now": false}`. Строки нет, если таких финишей нет. Заодно это ограничивает шкалу, если страницу открыть через несколько дней после гонки;
  - конец — как у прогноза: `max(floor_hour(now) + 1ч, ceil_hour(последний КВ команд в прогнозе))`.
- Строка: `{"from": "15:00", "to": "16:00", "arrived": 12, "expected": 9.3, "now": true}`.
  - прошедший час (`to <= floor_hour(now)`): `arrived` — целое, в том числе `0`; `expected` — `null`;
  - текущий час (`from = floor_hour(now)`): `arrived` — финиши в `(floor_hour(now), now]`, `expected` — первая корзина прогноза `(now, следующий час]`; `now: true`; подпись — полный час;
  - будущий час: `arrived` — `null`, `expected` — число (0.1).
- `arrived` — сумма `people` команд со `state == finished`, по корзине `(from, to]` их `finish_ms`. Учитываются **все** финиши, в том числе категории без КВ: кухня кормит всех. Финиш позже `now` (часы спешат) идёт в текущий час. Финиш ровно в `HH:00` относится к часу, который в `HH:00` заканчивается.
- Результат: `{"all": rows, "<id>": rows, …}` для каждого id из `category_ids`, на одной сетке. Сетка никогда не пустая: минимум одна строка текущего часа.
- Сопоставление с прогнозом — по позиции: корзина прогноза `i` → строка шкалы `current_index + i`, где `current_index` — строка текущего часа. Корзина 0 (`now..след. час`) ложится в текущий час, остальные совпадают с будущими часами по границам. Конец шкалы считается один раз: `max(floor_hour(now) + 1ч, ceil_hour(последний КВ команд в прогнозе))`, и сетка прогноза с ним совпадает.
- Пустой прогноз (`_forecast_totals` вернул `[]`): у текущего часа `expected = 0.0`, будущих строк нет. Никакого `IndexError`.
- `expected` в JSON — округление до 0.1 (как сейчас).

### `_finish_rows(race, now_ms)`
- Тело нынешнего цикла из `RaceFinishesDataView.get` без изменений поведения. В `forecast_teams` добавляется `finish_ms`.
- Принимает необязательную категорию, чтобы печатный лист мог отфильтровать команды. Шкала на печати строится только по командам выбранной категории.

### Округление (одно правило для печати и JS)
- Целое `≈ N` = половина вверх от значения, уже округлённого до 0.1: на сервере `int(x + 0.5)`, в JS `Math.round(x)`. Встроенный `round()` в Python не годится: он банковский (`round(2.5) == 2`), и лист разошёлся бы со страницей.
- «Всего ≈» = половина вверх от суммы 0.1-значений строк, а не сумма округлённых строк. Так считает и нынешний `renderForecast`.
- `arrived` — целые, без округления.

### JSON `race_finishes_data`
- `forecast` → `timeline` в формате выше. Остальные ключи без изменений.

### Печатный лист `race_finishes_print`
- `race/<slug:race_slug>/finishes/print/`, гейт `_load_race_for_admin`, `?category=<id>` через `_selected_category`.
- Шаблон `src/templates/race/finishes_print.html`, standalone, как `checklist.html`: `.toolbar` с выбором категории, «← Финиш» и кнопкой «Печать» (`onclick="window.print()"`, как в чек-листе), не печатается. CSS `src/static/css/finishes_print.css`. Других скриптов нет, `<script>` в HTML нет.
- Содержимое:
  - заголовок «<Гонка> — приход людей на финиш»; строка «на 15:20, 9 октября»: view передаёт `timezone.localtime(now)`, шаблон форматирует `|date:"H:i, j E"` (`E` — родительный падеж месяца по `LANGUAGE_CODE`; это единственная логика в шаблоне); при фильтре — « · категория <код>»;
  - таблица «Час | Пришло | Ожидается». `expected` выводится как `≈ N` (целое), `null` — пустая ячейка;
  - строка текущего часа выделена рамкой, справа «← сейчас»; строка «раньше» подписана «до HH:MM»;
  - «Всего»: сумма `arrived` и `≈` сумма `expected`;
  - мелкая сноска: «Прогноз: в среднем за 40 мин до своего КВ, разброс 30 мин. Не учтены: не стартовали — K чел., опаздывающие больше часа — N чел., без КВ на дистанции — M чел.». Части с нулём не выводятся. K, N, M считаются по `forecast_teams` (`state`, `overdue_long`, `people`) с учётом `?category=`.
  - нет оплаченных команд → таблица из одной строки текущего часа с нулями, без ошибки.
- Числа (`≈` целые, итоги, N и M) считает view, в шаблоне логики нет.
- CSS: `@page { size: A4 portrait; margin: 15mm }`, шрифт таблицы около 16pt, `.toolbar` скрыта в `@media print`.

### Живая страница
- `renderForecast` → `renderTimeline` из `data.timeline[filter || "all"]`.
- Столбцы: час | пришло | ожидается (`≈`) | полоса. Полоса — два сегмента: `arrived` и `expected`, разными оттенками, масштаб — по максимуму суммы. Текущий час выделен.
- Последняя строка — «Всего»: сумма `arrived` и `≈` по правилу округления выше.
- Пустое состояние «Пока никого» — когда нет строк с `arrived > 0` и сумма `expected < 0.05`.
- Заголовок карточки — «Приход людей по часам». Подпись о модели остаётся.
- Ссылка «Печать для кухни» (`target="_blank"`) в шапке карточки. Её базовый URL — в config-острове (`printUrl`). JS добавляет `?category=<id>` при выбранном фильтре.

## What Goes Where
- **Implementation Steps**: код, тесты, CLAUDE.md.
- **Post-Completion**: печать на реальном принтере, проверка живой страницы руками.

## Implementation Steps

### Task 1: `build_timeline` в `finish_forecast.py`

**Files:**
- Modify: `src/apps/race/finish_forecast.py`
- Modify: `src/apps/race/test_finishes.py`

- [ ] вынести ядро `build_forecast` в `_forecast_totals` (мс-границы + неокруглённые значения); `build_forecast` — обёртка, его тесты не меняются
- [ ] функция сетки шкалы: от часа первого финиша (не раньше `floor_hour(now) − 24ч`) до конца сетки прогноза; строка «раньше» для старых финишей
- [ ] `build_timeline`: `arrived` по финишам `(from, to]`, финиш в будущем → текущий час, `expected` по позиции из `_forecast_totals` (корзина `i` → строка `current_index + i`), пустой прогноз → `expected=0.0`, флаг `now`, null-правила
- [ ] ключи `all` + каждая категория на одной сетке
- [ ] тесты: шкала начинается с часа первого финиша; финиш ровно в 15:00 → 14:00–15:00; финиш в будущем → текущий час; финиш команды без КВ учтён; прошлые часы `expected is None`, будущие `arrived is None`, текущий — оба и `now: true`; ни финишей, ни людей на дистанции → одна строка текущего часа с `arrived=0`, `expected=0`
- [ ] тесты: сумма `expected` в `timeline` = сумма `build_forecast` (с допуском округления); финиш в 1970 году и финиш двое суток назад → шкала не длиннее 24 ч + строка «раньше» с их людьми; `now` ровно в `HH:00` → в текущем часе `arrived=0`, финиш в `now` → в предыдущем часе; финиш ровно в `now` (не на часе) → текущий час; опаздывающий ≤ 60 мин → в `expected` текущего часа; `arrived` по категории считает только её команды; шкала через полночь (метки повторяются, сопоставление не ломается); пустой прогноз → нет `IndexError`
- [ ] прогнать тесты — зелёные

### Task 2: `_finish_rows` и `timeline` в JSON

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/test_finishes.py`

- [ ] вынести цикл из `RaceFinishesDataView.get` в `_finish_rows(race, now_ms, category=None)`, добавить `finish_ms` в `forecast_teams`
- [ ] `RaceFinishesDataView` отдаёт `timeline` вместо `forecast`
- [ ] обновить `test_finishes_data_forecast`: ключ `timeline`, ключи категорий, `arrived` у финишировавшей команды в нужном часе; команда с мусорным `finish_time` в `arrived` не попадает
- [ ] остальные тесты эндпоинта без изменений — зелёные

### Task 3: печатный лист `race_finishes_print`

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/finishes_print.html`
- Create: `src/static/css/finishes_print.css`
- Modify: `src/apps/race/test_finishes.py`

- [ ] `RaceFinishesPrintView`: гейт, `_selected_category`, `_finish_rows`, `build_timeline(...)["all" или str(id)]`, готовые строки для шаблона (`≈ N` половиной вверх, пустые ячейки), итоги по правилу округления, K/N/M для сноски, `localtime(now)` для даты
- [ ] URL `race/<slug:race_slug>/finishes/print/` → `race_finishes_print`
- [ ] шаблон по образцу `checklist.html`: `.toolbar`, заголовок, таблица, «← сейчас», «Всего», сноска; без `<script>`
- [ ] `finishes_print.css`: A4, крупная таблица, рамка текущего часа, `.toolbar` скрыта при печати
- [ ] тесты доступа: аноним → `login`; plain, MODERATOR, суперюзер без `RaceAdmin` → 403; ADMIN → 200
- [ ] тесты содержимого: строки часов и итоги в HTML; «← сейчас» ровно один раз; итоги печати с `?category=X` равны итогам `timeline["X"]` из JSON; мусорный `?category=` → вся гонка; в HTML нет `<script`; сноска называет опаздывающих больше часа и не стартовавших; гонка без оплаченных команд → 200 и одна строка; `expected = 2.5` печатается как `≈ 3` (половина вверх)
- [ ] прогнать тесты — зелёные

### Task 4: живая страница — шкала и кнопка печати

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/finishes.html`
- Modify: `src/static/js/finishes.js`
- Modify: `src/static/css/finishes.css`
- Modify: `src/apps/race/test_finishes.py`

- [ ] `printUrl` в `finishes_config`; ссылка «Печать для кухни» в карточке; заголовок «Приход людей по часам»
- [ ] `renderTimeline`: столбцы час | пришло | ожидается | двухцветная полоса, выделение текущего часа, «Всего»; обновление `href` ссылки печати при смене фильтра
- [ ] стили новых столбцов, двух сегментов полосы и текущего часа
- [ ] тест: в HTML страницы есть ссылка на `race_finishes_print`, а config-остров содержит `printUrl`
- [ ] прогнать тесты — зелёные; проверка JS руками

### Task 5: Verify acceptance criteria
- [ ] все пункты Overview и Technical Details реализованы
- [ ] крайние случаи: никто не финишировал, все финишировали, финиш в будущем, категория без КВ, фильтр по категории на печати и на странице
- [ ] `uv run pytest` — весь набор зелёный
- [ ] `make lint` — чисто
- [ ] нет внешних ресурсов на печатном листе и странице

### Task 6: [Final] Update documentation
- [ ] абзац «Финиш» в `CLAUDE.md`: `timeline` вместо `forecast`, правила `arrived`/`expected`, `_finish_rows`, печатный лист `race_finishes_print` (standalone, `?category=`, без JS)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- на тестовой гонке с финишами в разные часы открыть «Финиш»: в прошлых часах «пришло», в текущем — оба числа, в будущих — «ожидается»
- сменить фильтр категории → ссылка «Печать для кухни» открывает лист этой категории
- распечатать (или «Сохранить как PDF»): один лист A4, панель не печатается, текущий час выделен, числа читаются с расстояния
- ширина телефона: живая таблица не вылезает за экран
