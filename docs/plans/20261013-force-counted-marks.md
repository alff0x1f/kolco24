# Ручное засчитывание неполных NFC-отметок

## Overview
- Протокол (`src/apps/race/results.py:_score_checkpoints`) засчитывает NFC-отметку только при `method="nfc"`,
  `verified=True` и числе разных непустых `MarkPresent.nfc_uid >= team.ucount`. Неполную отметку (1 чип из 3)
  судья засчитать не может — правка `verified` в БД ничего не даёт.
- Добавляем флаг судьи `Mark.force_counted` (+ кто/когда), кнопку «Засчитать / Снять» на странице команды
  «Данные приложения» и строку «неполные: 5, 12» в протоколе (только для админов гонки).
- Правило для остальных отметок не меняется; флаг прощает неполный состав, но не отсутствие `verified`.

## Context (from discovery)
- `src/apps/mobile/models.py` — `Mark`, явный список `MARK_UPDATE_FIELDS` (upsert с телефона); последняя миграция
  `0012_trackpoint_mobile_tp_race_team_ts`.
- `src/apps/race/results.py` — `_score_checkpoints`, `_build_row`; `src/apps/race/models.py:ProtocolRow`; последняя
  миграция `0005_racepromo`.
- `src/apps/race/views.py` — `RaceAppDataTeamView` (гейт `can_edit_race`), `ProtocolView` (final виден всем).
- `src/apps/race/app_data.py:build_team_timeline` — события `kind="mark"`; шаблон
  `src/templates/race/app_data_team.html`.
- `src/templates/race/protocol.html`, `src/static/css/protocol.css` (есть блок `@media print`).
- `src/website/urls.py:203` — `race_app_data_team`.
- Тесты: `src/apps/race/tests.py` (уже импортирует `build_protocol`, `build_team_timeline`),
  `src/apps/mobile/tests.py` (ingest `/marks/`).

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change (`uv run pytest --reuse-db <file>`)
- maintain backward compatibility

## Testing Strategy
- **unit tests**: pytest-style функции с `@pytest.mark.django_db`, фикстуры `client`/`django_user_model`.
- e2e тестов в проекте нет.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview
- Флаг живёт на `Mark`, а не в снимке: снимок пересобирается из живых данных, флаг переживает пересборку.
- Поля флага **не** входят в `MARK_UPDATE_FIELDS` — повторная загрузка той же отметки с телефона их не трогает.
- `Mark` в `/admin/` не регистрируем; правка только на сайте, гейт `can_edit_race` (только `RaceAdmin` ADMIN).
- «Неполные» хранятся в снимке (`ProtocolRow.nfc_incomplete_checkpoints`), чтобы протокол не читал живые данные.
- После нажатия «Засчитать» протокол нужно пересобрать вручную (сообщение об этом).

## Technical Details
- `Mark.force_counted = BooleanField(default=False)`, `Mark.forced_by = FK(AUTH_USER_MODEL, null=True, blank=True,
  on_delete=SET_NULL, related_name="+")`, `Mark.forced_at = DateTimeField(null=True, blank=True)`.
- Подсчёт: `verified and (present_real >= ucount or force_counted)`.
- Неполные КП: номера КП (по `cp_info`), у которых есть `nfc`+`verified` отметка, отброшенная только из-за состава,
  и которые в итоге не засчитаны ни по NFC, ни по фото. Порядок — как у `nfc_points` (по времени первой отметки),
  формат `", ".join`.
- Endpoint: `POST race/<slug:race_slug>/app-data/team/<int:team_id>/mark-count/`, имя `race_app_data_mark_count`,
  поля `mark_id` и `action=set|unset` (другое → 400). `mark_id` — в теле, не в пути: `Mark.id` — произвольная строка
  клиента (может содержать `/`), и `{% url %}` с ней упал бы `NoReverseMatch` → 500 всей страницы команды.
- Якорь: `mark_anchor(mark_id)` в `app_data.py` — `"mark-" + blake2b(id, digest_size=6).hexdigest()`; один helper для
  шаблона и redirect, сырой `id` в HTML-атрибут/фрагмент не попадает.
- PRG: `redirect(reverse("race_app_data_team", ...) + "#" + mark_anchor(mark.pk))`.
- `set` требует `method="nfc"`, `verified=True` и КП из `cp_info` (известный, `cost >= 0`). `unset` разрешён
  **всегда**: `verified`/`method`/`checkpoint_id` пересчитываются при повторной загрузке, и засчитанная отметка может
  стать `verified=False` — снять флаг должно быть можно.
- `present_real` на странице считается **так же, как в `_score_checkpoints`**: разные сырые непустые `nfc_uid`
  (без `_norm_uid`), иначе `04ab`/`04AB` дадут разные «полная/неполная» на странице и в протоколе.
- Неполные КП дедуплицируются по `checkpoint_id` (не по `number` — он не уникален в гонке), порядок — по первому
  появлению в NFC-цикле (по времени).

## What Goes Where
- **Implementation Steps**: код, миграции, шаблоны, CSS, тесты, CLAUDE.md.
- **Post-Completion**: ручная проверка на реальной гонке, деплой миграций.

## Implementation Steps

### Task 1: Поля флага на `Mark`

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0013_mark_force_counted.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] добавить `force_counted`, `forced_by`, `forced_at` в `Mark`; в docstring одна фраза: поля судьи, не в
      `MARK_UPDATE_FIELDS`
- [ ] `uv run python src/manage.py makemigrations mobile --name mark_force_counted`
- [ ] тест: повторный POST `/marks/` с тем же `id` не сбрасывает `force_counted`/`forced_by`/`forced_at`
- [ ] тест: `MARK_UPDATE_FIELDS` не содержит этих трёх полей
- [ ] run tests - must pass before next task

### Task 2: Подсчёт с флагом и «неполные» в снимке

**Files:**
- Modify: `src/apps/race/results.py`
- Modify: `src/apps/race/models.py`
- Create: `src/apps/race/migrations/0006_protocolrow_nfc_incomplete_checkpoints.py`
- Modify: `src/apps/race/tests.py`

- [ ] `_score_checkpoints`: засчитывать при `present_real >= ucount or force_counted`; собирать отброшенные из-за
      состава КП; после фото-прохода исключить засчитанные; вернуть `nfc_incomplete_points`
- [ ] обновить docstring `_score_checkpoints` (правило флага и неполных)
- [ ] `ProtocolRow.nfc_incomplete_checkpoints = TextField(blank=True, default="")` + миграция; заполнить в `_build_row`
- [ ] тесты: неполная без флага → в неполных, без очков; с `force_counted` → засчитана, не в неполных;
      `force_counted` + `verified=False` → не засчитана и не в неполных
- [ ] тесты: КП позже взят полной отметкой или фото → не в неполных; две неполные на один КП → номер один раз
- [ ] run tests - must pass before next task

### Task 3: Endpoint «Засчитать / Снять»

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] `RaceAppDataMarkCountView.post` — гейт как у `RaceAppDataTeamView`, но аноним → login с
      `?next=<race_app_data_team url>` (не POST-url, иначе после входа 405); team 404 вне гонки;
      `get_object_or_404(Mark, pk=request.POST["mark_id"], race=race, team=team)`; `action` вне `set|unset` → 400
- [ ] `set`: `method != "nfc"`, `verified=False` или КП не в гонке/`cost < 0` → без изменений, `messages.error`;
      `unset` — всегда; повторный `set` перезаписывает `forced_by`/`forced_at`
- [ ] номер КП для сообщения — из `Checkpoint`, fallback `id <checkpoint_id>`
- [ ] set/unset + `save(update_fields=["force_counted", "forced_by", "forced_at"])`; `messages.success`
      «КП N засчитан/снят. Пересоберите протокол»; redirect с якорем `#mark-<id>`
- [ ] url `race_app_data_mark_count` рядом с `race_app_data_team`
- [ ] тексты сообщений в тестах проверять через `get_messages` (рендер на странице появится в Task 4)
- [ ] тесты: аноним → login `?next=<страница команды>`, не-админ → 403, суперюзер без `RaceAdmin` → 403, модератор → 403, GET → 405
- [ ] тесты: чужая команда / отметка другой команды → 404; `verified=False` не меняется; set ставит
      `forced_by`/`forced_at`, unset сбрасывает; плохой `action` → 400; `set` на неизвестный КП не меняется;
      засчитанная отметка, ставшая `verified=False`, снимается через `unset`
- [ ] run tests - must pass before next task

### Task 4: Кнопки на странице команды

**Files:**
- Modify: `src/apps/race/app_data.py`
- Modify: `src/templates/race/app_data_team.html`
- Modify: CSS страницы «Данные приложения» (найти в `extra_head` шаблона)
- Modify: `src/apps/race/tests.py`

- [ ] helper `mark_anchor(mark_id)`; `build_team_timeline`: в событие `mark` добавить `mark_id`, `anchor`,
      `present_real` (разные **сырые** непустые `nfc_uid` из уже загруженных `presents` — как в `_score_checkpoints`),
      `can_force` (nfc + verified + КП известен и `cost >= 0`),
      `force_counted`, `forced_by` (имя), `forced_at` (форматировать на сервере), `incomplete`; `ucount` в контекст;
      `select_related("forced_by")` без лишних запросов
- [ ] шаблон: `id="{{ ev.anchor }}"` у строки; `mark_id` — hidden input формы; если `force_counted` — бейдж
      «засчитана вручную: кто, когда» + форма «Снять» (**при любом** `verified`/`method`); иначе если `can_force` и
      неполная — бейдж «неполная X/ucount» + форма «Засчитать» (POST, `{% csrf_token %}`)
- [ ] вывод `messages` наверху страницы
- [ ] тесты `build_team_timeline`: поля события для полной, неполной и засчитанной отметки
- [ ] тест: `present_real` при `04ab` + `04AB` совпадает с подсчётом протокола
- [ ] тест страницы: «Засчитать» есть у неполной verified-отметки, нет у полной, у `verified=False` и у неизвестного
      КП; «Снять» есть у засчитанной `verified=False`; `Mark.id` с `/` и `"<>` не ломает страницу (200)
- [ ] run tests - must pass before next task

### Task 5: Ссылки и «неполные» в протоколе

**Files:**
- Modify: `src/templates/race/protocol.html`
- Modify: `src/static/css/protocol.css`
- Modify: `src/apps/race/tests.py`

- [ ] при `can_edit`: название команды — `<a class="protocol-link">` на `race_app_data_team`; иначе простой текст
- [ ] при `can_edit` и непустом `nfc_incomplete_checkpoints`: строка «неполные: …» под «Чип», ссылка на страницу
      команды (класс `protocol-incomplete`)
- [ ] CSS (всё под `.protocol-page`, как остальной файл): стиль ссылки и строки на экране; в `@media print` —
      `.protocol-page .protocol-link { color: inherit; text-decoration: none; }`,
      `.protocol-page .protocol-incomplete { display: none !important; }`
- [ ] ссылка строится по `ProtocolRow.team_id`; команда, удалённая после сборки, даст 404 — допустимо
- [ ] тесты: админ видит ссылку и «неполные» в черновике; аноним на `final` — ни ссылки, ни «неполных»
- [ ] run tests - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] неполная verified-отметка: «Засчитать» → «Пересобрать» → КП в протоколе с очками
- [ ] «Снять» → «Пересобрать» → КП снова в «неполных»
- [ ] `make format && make lint`
- [ ] run full test suite: `uv run pytest`

### Task 7: [Final] Update documentation
- [ ] CLAUDE.md: одна короткая строка про `force_counted` (не в `MARK_UPDATE_FIELDS`, ставится на странице
      команды «Данные приложения», гейт `can_edit_race`, учитывается в протоколе только при `verified`); поправить
      «no admin UI today» в разделе **Marks upload**, чтобы не противоречило
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- на реальной гонке засчитать неполную отметку, пересобрать протокол, проверить очки и место
- распечатать протокол (Ctrl+P): ссылки выглядят как текст, «неполные» не видны

**Deploy**:
- две миграции (`mobile/0013`, `race/0006`) — только добавление полей, безопасно
- уже замороженный `final` протокол не меняется; чтобы применить флаги, нужна пересборка (новый draft) и заморозка
