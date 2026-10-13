# Снятие команды с зачёта в протоколе

## Overview
- Судья должен иметь возможность снять команду с зачёта: сильное превышение КВ, доехали на машине, сошёл участник,
  вышли не все и т.п.
- Снятая команда остаётся в протоколе категории внизу, без места («сн.»), с причиной. Баллы и время видны для справки.
- Решение хранится на живой `Team`, поэтому пересборка черновика протокола его не теряет. Ставится на странице команды
  в app-data, как кнопка «засчитать вручную» (`force_counted`). Протокол после этого пересобирают руками.

## Context (from discovery)
- `src/website/models/models.py` — `Team.dnf` уже есть (`BooleanField`, правится в `/admin/` через `TeamAdmin`).
  `Team.update_places` ставит `dnf`-командам `place=10000`, но его никто не вызывает — не трогаем.
- `src/apps/race/models.py` — `ProtocolRow.dnf` уже есть, `build_protocol` его копирует, но нигде не использует.
- `src/apps/race/results.py` — `_build_row`, `_assign_places`, `build_protocol`.
- `src/apps/race/views.py` — `ProtocolView` читает строки через `order_by("place")` (~1654); `RaceAppDataTeamView` (~2464), `RaceAppDataMarkCountView` (~2492) — образец POST-действия.
- `src/website/urls.py:199-211` — маршруты app-data (`race_app_data`, `race_app_data_team`, `race_app_data_mark_count`).
- `src/templates/race/app_data_team.html` — шапка команды (`.head`), форма `force-form` (~125).
- `src/templates/race/app_data.html` + `src/apps/race/app_data.py` (`build_overview`, `_race_teams`) — обзор команд.
- `src/templates/race/protocol.html` (колонка места ~106, «Итог» ~128), `src/static/css/protocol.css`,
  `src/static/css/app_data.css`.
- Тесты: `src/apps/race/tests.py` (там уже есть тесты `build_protocol` и `race_app_data_mark_count`).
- Последние миграции: `website/0099_race_map_url`, `race_app/0006_protocolrow_nfc_incomplete_checkpoints`.

## Development Approach
- **testing approach**: Regular (сначала код задачи, потом тесты в той же задаче)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest --reuse-db src/apps/race/tests.py`
- maintain backward compatibility

## Testing Strategy
- **unit tests**: pytest-функции с `@pytest.mark.django_db`, в `src/apps/race/tests.py`, в стиле существующих тестов
  протокола и `mark-count`.
- e2e-тестов в проекте нет; шаблоны проверяем через `client.get` и поиск строк в ответе.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- Флаг — существующий `Team.dnf`. Рядом три новых поля: причина, кто снял, когда.
- `ProtocolRow` получает `dnf_reason` (снимок причины). `dnf` уже копируется.
- `_assign_places` ставит снятые команды в конец категории, `place=0`, они не занимают номер места.
- `ProtocolView` сортирует так, чтобы `place=0` шёл последним; порядок снятых между собой — по `id` (порядок
  `bulk_create` = порядок `_assign_places`).
- «Снята» в протоколе = `row.dnf and not row.place`. Так старые финальные строки с `dnf=True` и настоящим местом
  (если такие есть) выглядят как раньше.
- Новое POST-действие `race_app_data_team_dnf` на странице команды в app-data: `set` (причина обязательна) / `unset`.
- Протокол: «сн.» вместо места, «Снята: <причина>» под названием, «-» в «Итог», строка бледнее.
- Обзор app-data: бейдж «снята» у команды.

## Technical Details
- `Team`:
  - `dnf_reason = models.CharField(max_length=200, blank=True, default="")`
  - `dnf_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")`
  - `dnf_at = models.DateTimeField(null=True, blank=True)`
- `ProtocolRow.dnf_reason = models.CharField(max_length=200, blank=True, default="")`
- Сортировка в `_assign_places`: `(category_id, dnf, -final_score, duration_ms, team_id)`. Счётчик места сбрасывается на
  смене категории и растёт только для не-`dnf` строк; `dnf`-строкам `place=0`.
- View `RaceAppDataTeamDnfView.post(request, race_slug, team_id)`:
  - аноним → `login?next=<team page>`; нет `can_edit_race` → 403; команда не из гонки → 404;
  - `action` не `set`/`unset` → 400;
  - `set`: `reason = request.POST.get("reason", "").strip()[:200]`; пустая → `messages.error("Укажите причину снятия.")`, редирект,
    ничего не пишем; иначе `dnf=True`, `dnf_reason=reason`, `dnf_by=user`, `dnf_at=now()`;
  - `unset`: `dnf=False`, `dnf_reason=""`, `dnf_by=None`, `dnf_at=None`;
  - `team.save(update_fields=["dnf", "dnf_reason", "dnf_by", "dnf_at", "updated_at"])` — `updated_at` также двигает
    `teams_version` (ETag мобильных команд); это нормально;
  - сообщение «Команда снята. Пересоберите протокол.» / «Команда возвращена. Пересоберите протокол.»;
  - редирект на `race_app_data_team` + `#team-dnf`.
- `ProtocolView`: `rows = sorted(protocol.rows.filter(category_id=…), key=lambda r: (r.place == 0, r.place, r.id))`
  (или `order_by(Case(When(place=0, then=1), default=0), "place", "id")`).
- URL: `race/<slug:race_slug>/app-data/team/<int:team_id>/dnf/`, имя `race_app_data_team_dnf`, в `website/urls.py`
  рядом с `race_app_data_mark_count`.

## What Goes Where
- **Implementation Steps**: модели, миграции, сервис протокола, view, шаблоны, CSS, тесты, CLAUDE.md.
- **Post-Completion**: ручная проверка на живой гонке.

## Implementation Steps

### Task 1: Поля снятия на Team и ProtocolRow

**Files:**
- Modify: `src/website/models/models.py`
- Create: `src/website/migrations/0100_team_dnf_reason.py`
- Modify: `src/apps/race/models.py`
- Create: `src/apps/race/migrations/0007_protocolrow_dnf_reason.py`
- Modify: `src/website/admin.py`

- [ ] добавить `dnf_reason`, `dnf_by`, `dnf_at` в `Team` рядом с `dnf`
- [ ] добавить `dnf_reason` в `ProtocolRow` рядом с `dnf`
- [ ] сгенерировать миграции: `makemigrations website --name team_dnf_reason` и
      `makemigrations race_app --name protocolrow_dnf_reason`; проверить, что они только схемные
- [ ] `TeamAdmin`: `readonly_fields` += `("dnf_by", "dnf_at")` (иначе `dnf_by` — `<select>` всех пользователей)
- [ ] тестов в этой задаче нет (только схема); поля покрываются тестами задач 2–3
- [ ] run tests - must pass before next task

### Task 2: Снятые команды в build_protocol

**Files:**
- Modify: `src/apps/race/results.py`
- Modify: `src/apps/race/tests.py`

- [ ] `_build_row` копирует `team.dnf_reason` в `ProtocolRow.dnf_reason`
- [ ] `_assign_places`: новый ключ сортировки, `place=0` для `dnf`, номер места не тратится; обновить docstring
- [ ] write test: снятая команда с лучшим баллом — в конце категории, `place=0`, остальные 1..N без пропуска
- [ ] write test: `dnf_reason` скопирован; две снятые команды упорядочены по баллам между собой
- [ ] write test: пересборка черновика сохраняет снятие; после снятия флага и пересборки команда снова получает место
- [ ] write test: снятие в одной категории не сдвигает места в другой
- [ ] run tests - must pass before next task

### Task 3: Действие «Снять / Вернуть» в app-data

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] добавить `RaceAppDataTeamDnfView` после `RaceAppDataMarkCountView` по той же схеме доступа
- [ ] зарегистрировать URL `race_app_data_team_dnf`
- [ ] write tests доступа: аноним → логин с `next` на страницу команды; не-админ → 403; суперпользователь без
      `RaceAdmin(ADMIN)` → 403; команда другой гонки → 404; плохой `action` → 400; GET → 405
- [ ] write tests логики: `set` пишет 4 поля и двигает `updated_at`; `set` с пустой/пробельной причиной и без ключа
      `reason` ничего не пишет и показывает ошибку; `unset` очищает все поля; редирект на `#team-dnf`
- [ ] run tests - must pass before next task

### Task 4: Блок снятия на странице команды и бейдж в обзоре

**Files:**
- Modify: `src/templates/race/app_data_team.html`
- Modify: `src/templates/race/app_data.html`
- Modify: `src/static/css/app_data.css`
- Modify: `src/apps/race/tests.py`

- [ ] в шапке команды блок `id="team-dnf"`: не снята — поле «Причина» (`maxlength=200`, `required`) + «Снять с зачёта»;
      снята — бейдж «Снята: <причина> — <кто>, <когда>» + «Вернуть»; CSRF-токен в обеих формах
- [ ] в обзоре — бейдж «снята» у снятой команды через `{% if row.team.dnf %}` (`_race_teams` уже отдаёт `Team`);
      `dnf_by` в обзоре не выводить — это +1 запрос на команду
- [ ] стили бейджа и формы в `app_data.css`
- [ ] write test: страница команды показывает форму для не снятой и бейдж с причиной для снятой команды
- [ ] write test: обзор показывает «снята» только у снятой команды
- [ ] run tests - must pass before next task

### Task 5: Отображение снятых команд в протоколе

**Files:**
- Modify: `src/apps/race/views.py` (`ProtocolView`)
- Modify: `src/templates/race/protocol.html`
- Modify: `src/static/css/protocol.css`
- Modify: `src/apps/race/tests.py`

- [ ] `ProtocolView`: строки с `place=0` — в конце, дальше по `id`; обновить docstring
- [ ] у снятой строки (`row.dnf and not row.place`): класс `row-dnf`, «сн.» в колонке места, «Снята: <причина>» мелко под названием, «-» в «Итог»
- [ ] `.row-dnf` бледнее, в том числе в `@media print`
- [ ] write test: на странице протокола снятая команда с лучшим баллом выводится после команд с 1-м и 2-м местом
      (индекс имени в `response.content`)
- [ ] write test: строка с `dnf=True` и настоящим `place` (старые данные) показывает место, а не «сн.»
- [ ] write test: итоговый протокол (аноним) показывает «сн.» и причину, итог снятой команды не выводится
- [ ] write test: обычная команда по-прежнему показывает место и итог
- [ ] run tests - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] все требования из Overview выполнены
- [ ] граничные случаи: пустая причина, команда другой гонки, снятие в финальном протоколе видно всем
- [ ] `make format` и `make lint`
- [ ] полный прогон: `uv run pytest`

### Task 7: [Final] Update documentation
- [ ] CLAUDE.md: 1–2 предложения в абзаце **Protocol snapshot** — снятие хранится на `Team.dnf`/`dnf_reason`, ставится на
      странице команды в app-data (`race_app_data_team_dnf`), снятые идут в конец категории с `place=0`, нужна пересборка
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Before deploy**:
- на проде проверить `Team.objects.filter(dnf=True).count()` и
  `ProtocolRow.objects.filter(dnf=True, protocol__status="final").count()`. Если команды есть — после пересборки они
  уйдут вниз без причины: снять флаг или вписать причину. Финальные строки защищены правилом `dnf and not place`.

**Manual verification**:
- на тестовой гонке снять команду, пересобрать черновик, проверить протокол на экране и при печати
- вернуть команду, пересобрать, проверить, что место восстановилось
- зафиксировать протокол и открыть его анонимно — причина видна

**Out of scope** (решено в брейншторме): история решений, список готовых причин, автоснятие за превышение КВ, снятие
прямо из протокола, автоматическая пересборка.
