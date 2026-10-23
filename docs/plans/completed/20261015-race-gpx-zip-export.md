# Выгрузка всех GPX-треков гонки (ZIP)

## Overview
- Организатор скачивает одним ZIP-архивом GPX-треки всех команд гонки — по одному `.gpx` на команду, разложенных по
  папкам категорий. В архив попадает каждая неудалённая команда, у которой есть точки в окне (оплата не важна).
- Трек каждой команды обрезан окном `[start_time − 10 мин, finish_time + 10 мин]` и очищен тем же фильтром
  выбросов `trackLines`, что в iOS/Android-приложении. Дорога до старта и обратно и GPS-скачки в файл не попадают.
- Встраивается в страницу «Карта гонки» (`race_map`): новая ссылка «Все треки (ZIP)», тот же гейт `can_edit_race`.

## Context (from discovery)
- `src/apps/race/views.py`: `RaceMapGpxView` (:2248, GPX одной команды, сырой трек, гейт `_load_and_authorize`),
  `_gpx_time` (:2225), `_add_track` (:2345, `ET`-сборка `<trk>/<trkseg>/<trkpt>`).
- `src/apps/mobile/models.py:86` `TrackPoint`: `install_id`, `segment_id`, `lat`, `lon`, `accuracy`, `altitude`,
  `gps_time_ms`, `trusted_ms` (null), `elapsed_at`, `boot_count` (null), `id`. Нет `wall_ms`.
- `Team.start_time`/`finish_time` — BigInteger мс, `0` = не задано; пишутся из verified NFC-меток как
  `Coalesce(NullIf(trusted_ms, 0), wall_ms)`, first-write-wins (`apps/mobile/views.py` ~:774).
- Гейт: в `views.py` уже есть `_load_race_for_admin` (аноним → `login?next=`, не админ → 403) — переиспользуем.
- `src/website/urls.py:192` — маршрут `race_map_gpx`; `src/templates/race/map.html:44` — `#rmGpxInclude`.
- Фильтр-эталон: `ios-kolco24/kolco24/Core/Track/TrackLines.swift`,
  `android-kolco24/.../data/track/TrackModels.kt`; тестовые векторы: `android-kolco24/app/src/test/.../track/TrackLinesTest.kt`,
  `ios-kolco24/kolco24Tests/Core/TrackLinesTests.swift`.
- Тесты гонки лежат отдельными модулями: `src/apps/race/test_starts.py`, `test_finishes.py` (pytest-style).

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change; `make format` + `make lint` before commit
- maintain backward compatibility (существующий `race_map_gpx` не меняется)

## Testing Strategy
- **unit tests**: чистый фильтр без БД — `src/apps/race/test_track_filter.py`.
- **view tests**: `src/apps/race/test_gpx_all.py`, `@pytest.mark.django_db`, `client`/`django_user_model`.
- e2e-тестов в проекте нет.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview
- Синхронный view `RaceMapGpxZipView`, ZIP собирается в памяти (`BytesIO` + `zipfile.ZIP_DEFLATED`), команды
  обрабатываются по одной, чтобы в памяти не держать точки всей гонки — стриминг не нужен.
- Фильтр — отдельный чистый модуль `track_filter.py`, порт `trackLines` один в один, чтобы серверный GPX совпадал с
  тем, что видит участник в приложении.
- Фильтр запускается **по каждому телефону (`install_id`) отдельно** — как в приложении, где фильтр видит точки одного
  устройства; смешанные точки двух телефонов ломают тест достижимости.
- Фильтр всегда включён: сырой трек одной команды по-прежнему отдаёт существующая кнопка «GPX».

## Technical Details
- **Время точки**: `point_time_ms = trusted_ms or gps_time_ms` — `0` считается отсутствием, как `NullIf` при записи
  `start_time`. Замена `trustedMs ?? wallMs` из приложения (`wall_ms` на сервер не приходит). Если старт-метка была без
  `trusted_ms`, её время по часам телефона, а трека — по GPS; расхождение в минуты гасит запас ±10 мин. Точки с
  `point_time_ms <= 0` отбрасываются до фильтра. Поэтому `<time>` в ZIP может отличаться от `race_map_gpx`, где пишется
  `gps_time_ms` — это сознательно.
- **Сортировка**: время → `boot_count` (`None` → `-1`) → `elapsed_at` → `id`.
- **Порядок**: сначала фильтр по всему треку телефона, потом обрезка окном (так линии совпадают с приложением, которое
  фильтрует весь трек). Обрезка: из каждой линии оставить точки в окне, пустые линии выбросить.
- **Окно**: `GPX_ALL_MARGIN_MS = 600_000`. `lo = start_time − margin` если `start_time` иначе `−∞`;
  `hi = finish_time + margin` если `finish_time` иначе `+∞`, включительно. `start_time > finish_time` → окно может
  оказаться пустым, команда пропадает (не обрабатываем отдельно).
- **Фильтр** (`track_lines(points)`): константы `HARD_CAP_ACCURACY_M = 500`, `MAX_SPEED_MPS = 14`,
  `SHORT_CHAIN_MAX_POINTS = 3`, `SHORT_CHAIN_MAX_DURATION_MS = 60_000`, `TAIL_ACCURACY_RATIO = 3`,
  `TAIL_REFERENCE_MIN_ACCURACY_M = 1`, `EARTH_RADIUS_M = 6_371_000`.
  1. отбросить `accuracy > 500` (500 остаётся); 2. разрезать на последовательные раны `segment_id`; 3. внутри рана —
  цепочки по недостижимым шагам (`excess = max(0, d − min(acc_a, acc_b))`, `dt = max(|Δt|, 1000)`,
  `excess / dt_s <= 14` включительно); если цепочка одна — ран возвращается как есть (до правила головы);
  4. проход по цепочкам:
     - голова (`i == 0`): drop, если короткая, а `chains[1]` — нет;
     - длинная цепочка не дропается никогда;
     - короткая внутренняя: drop, если обход достижим из **последней принятой точки** (`lines[-1][-1]`, может быть из
       более ранней линии) до `chains[i+1][0]`; если ничего ещё не принято — не дропается;
     - короткий хвост: drop, если `prev_kept_long and median(chain) >= 3 * max(median(chains[i-1]), 1)` — сравнение с
       `chains[i-1]`, не с последней линией; `prev_kept_long = not drop and not short` обновляется на каждой итерации;
     - `is_short_chain`: `len <= 3 and (t_last − t_first) < 60_000` (строго); медиана при чётном числе — среднее двух;
  5. принятая цепочка присоединяется к последней линии, если достижима из её последней точки, иначе новая линия.
  Вход — точки с атрибутами `lat, lon, accuracy, segment_id, time_ms` (namedtuple/dataclass, без Django).
  Принятое расхождение: в приложениях `accuracy` — Float32, в Python — double; результат может отличаться только на
  точных границах, в тестах таких значений избегаем.
- **Запрос**: по запросу на команду — `TrackPoint.objects.filter(race_id=race.id, team_id=team.id).values_list(...)`
  только нужных полей (`id, install_id, segment_id, lat, lon, accuracy, altitude, gps_time_ms, trusted_ms, elapsed_at,
  boot_count`), попадает в индекс `mobile_tp_race_team_ts`. GPX команды собирается, пишется в ZIP и отпускается — в
  памяти одна команда, а не вся гонка (~300 тыс. строк дали бы 150–200 МБ на воркер). ~100 запросов — приемлемо.
  Команды — `Team.objects.filter(category2__race=race)` (TeamManager исключает удалённых), сортировка по `id`.
- **GPX команды**: `<metadata><name>` = «<race.name> — №<start_number> <teamname>» (как `team_label` в
  `RaceMapGpxView`); один `<trk>` на телефон, телефоны упорядочены по `point_time_ms` первой точки после фильтра и
  обрезки (tie-break `install_id`); телефон без точек после обрезки не выводится; имя
  `<trk>` — `team_label`, если телефон один, иначе `f"{team_label} · телефон {n}"`. Каждая линия — `<trkseg>`;
  `<trkpt lat lon>` + `<ele>` (если есть) + `<time>` через `_gpx_time(time_ms)`. Пустой `install_id` — отдельный телефон.
  Без `<wpt>`.
- **Папки по категориям**: файл команды лежит в `<folder>/`, где `folder` — `Category.code` (`team.category2`), из
  которого удалены `/`, `\` и управляющие символы, `strip()`. Кириллица допустима (`zipfile` ставит UTF-8 flag сам).
  Если код пустой после очистки или повторяется среди категорий гонки — `{code}-{category_id}` (пустой → `category-{id}`).
  Команды берём с `select_related("category2")`.
- **ZIP**: `number = re.sub(r"[^A-Za-z0-9_-]", "", start_number or "")`; имя файла `team-{number}-{id}.gpx`, если
  `number` непустой, иначе `team-{id}.gpx`; полный путь `<folder>/<file>`; архив `<race.slug>-tracks.zip`,
  `content_type="application/zip"`, `Content-Disposition: attachment`. Нет точек ни у кого → пустой ZIP, 200.
- **URL**: `race/<slug:race_slug>/map/gpx/all/`, имя `race_map_gpx_all`. Гейт — существующий `_load_race_for_admin`:
  аноним → `login?next=`, не `can_edit_race` → 403 (суперпользователь без `RaceAdmin(role=ADMIN)` → 403).

## What Goes Where
- **Implementation Steps**: фильтр, view, URL, ссылка в шаблоне, тесты, строка в CLAUDE.md.
- **Post-Completion**: ручная проверка на реальной гонке.

## Implementation Steps

### Task 1: Порт фильтра `trackLines`

**Files:**
- Create: `src/apps/race/track_filter.py`
- Create: `src/apps/race/test_track_filter.py`

- [x] создать `track_filter.py`: константы, `haversine_m`, `is_reachable`, `is_short_chain`, `_median_accuracy`,
      `_split_when`, `_run_lines`, `track_lines`; шапка со ссылкой на `TrackLines.swift`/`TrackModels.kt`
- [x] добавить `sort_key(point)` (время → `boot_count`, `None` → `-1` → `elapsed_at` → `id`) и
      `point_time_ms(trusted_ms, gps_time_ms)` = `trusted_ms or gps_time_ms`
- [x] тест `point_time_ms`: `trusted_ms=None` и `trusted_ms=0` → `gps_time_ms`
- [x] прочитать `TrackLinesTest.kt` и `TrackLinesTests.swift`, перенести их сценарии как тестовые векторы
- [x] тесты: выброс в середине, голова, хвост с плохой точностью дропается, хвост с нормальной точностью остаётся,
      `accuracy > 500`, разрыв по `segment_id`, `L X Y R` (оба остаются), одна точка, пустой вход
- [x] тесты граничных случаев: `accuracy = 0`, одинаковое время (пол 1 с), хвостовой пол медианы
- [x] `uv run pytest src/apps/race/test_track_filter.py` — должно пройти

### Task 2: View `RaceMapGpxZipView` и маршрут

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/apps/race/test_gpx_all.py`

- [x] `GPX_ALL_MARGIN_MS`, `RaceMapGpxZipView` с гейтом `_load_race_for_admin`
- [x] запрос точек на команду, отброс `point_time_ms <= 0`, деление по `install_id`, сортировка, `track_lines`,
      затем обрезка линий окном
- [x] сборка GPX на команду (`ET`, `_gpx_time`), нумерация телефонов, ZIP в `BytesIO`, заголовки ответа
- [x] маршрут `race/<slug:race_slug>/map/gpx/all/` → `race_map_gpx_all` рядом с `race_map_gpx`
- [x] тесты доступа: аноним → редирект на `login?next=`, не админ → 403, суперпользователь без `RaceAdmin` → 403, admin → 200 `application/zip`
- [x] тесты окна: точки до `start − 10 мин` и после `finish + 10 мин` отрезаны; `start_time = 0` / `finish_time = 0` — граница открыта
- [x] тесты содержимого: 2 телефона → 2 `<trk>` с именами «· телефон 1/2» без смешения точек; один телефон → имя без суффикса;
      фильтр применён (выброс отсутствует); `<trkseg>` на каждую линию
- [x] тесты времени: точка с `trusted_ms=0` датируется `gps_time_ms`; точка с `gps_time_ms=0` и без `trusted_ms`
      отброшена; телефоны пронумерованы по первой точке после фильтра
- [x] тесты набора: команда без точек, команда со всеми точками вне окна и удалённая команда отсутствуют; точки чужой
      гонки не попадают; одинаковый `start_number` даёт разные имена файлов; пустой `start_number` → `team-<id>.gpx`;
      пустой ZIP при отсутствии точек
- [x] тесты папок: команды двух категорий лежат в `<code>/`; кириллический код читается обратно из ZIP; одинаковые
      коды двух категорий → `{code}-{id}`; код с `/` очищен
- [x] `uv run pytest src/apps/race/test_gpx_all.py` — должно пройти

### Task 3: Ссылка на странице карты

**Files:**
- Modify: `src/templates/race/map.html`
- Modify: `src/static/css/race_map.css` (только если нужен стиль)
- Modify: `src/apps/race/test_gpx_all.py`

- [x] ссылка «Все треки (ZIP)» рядом с `#rmGpxInclude`, `{% url 'race_map_gpx_all' race.slug %}`,
      `title="Отфильтровано, от старта до финиша ±10 мин"`
- [x] тест: страница `race_map` для админа содержит URL `race_map_gpx_all`
- [x] `uv run pytest src/apps/race/` — должно пройти

### Task 4: Verify acceptance criteria
- [x] все требования из Overview реализованы
- [x] граничные случаи обработаны
- [x] `uv run pytest`
- [x] `make format` и `make lint`

### Task 5: [Final] Update documentation
- [x] одна короткая строка в CLAUDE.md в абзаце про «Карту гонки» (`race_map_gpx_all`, ZIP, фильтр — порт `trackLines`
      по телефону, затем окно ±10 мин; время `trusted_ms or gps_time_ms`, в отличие от `race_map_gpx`)
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
**Manual verification**:
- скачать ZIP на гонке с реальными треками, открыть несколько файлов в GPX-просмотрщике, сравнить с треком в приложении
  участника (режим фильтра) — линии должны совпадать внутри окна
- проверить время ответа на самой большой гонке (gunicorn `timeout` = 60 с в `src/gunicorn.conf.py`)
