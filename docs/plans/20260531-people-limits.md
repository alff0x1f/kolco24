# Ограничения по количеству человек (people limits) для гонки и категории

## Overview

Добавить лимиты участников на двух уровнях:

- **Гонка** (`Race`): максимум оплаченных людей на всё соревнование.
- **Категория** (`Category`): максимум оплаченных людей в категории.

Поведение при редактировании/регистрации команды:

- В полную категорию нельзя перейти.
- Если в категориях есть места — переходить можно **без увеличения** числа участников. Если гонка полная, нельзя из категории «2 человека» перейти в «3–4 человека» (рост состава запрещён, чистый переход 2→2 разрешён).
- При достижении лимита гонки `Race.reg_status` автоматически переключается в `sold_out` (только в момент подтверждения оплаты; авто-реоткрытия нет).

Интегрируется в существующий flow: валидация в `TeamForm.clean()` (используется и add, и edit), конфигурация лимитов на странице редактирования гонки, триггер `sold_out` в `update_team()`.

## Context (from discovery)

Файлы/компоненты:

- `src/website/models/race.py` — `Race` (есть `people_count()`/`team_count()`, нет лимита), `Category` (есть `min_people`/`max_people` per-team, нет лимита). Добавить поля `people_limit` и хелперы.
- `src/website/models/models.py` — `Team` (`ucount` declared vs `paid_people` paid; FK `category2`). `TeamManager.get_queryset()` фильтрует `is_deleted=False` (строки 297–299). `update_team()` инкрементирует `paid_people` (строка 289) — точка триггера авто `sold_out`.
- `src/website/forms.py` — `TeamForm` (строка 465). `clean()` (строка 602) уже валидирует размер per-category и карты. Сюда добавить gate капасити; расширить сигнатуру (`team` + `bypass_limits`).
- `src/website/views/views_.py` — `AddTeam` (строка 1812), `build_category_options` (1759), `build_team_form_context` (1784).
- `src/website/views/team.py` — `EditTeamView` (строка 14); `post()` уже имеет view-level guard на уменьшение `paid_people`/`map_count_paid`.
- `src/apps/race/forms.py` — `RaceForm.Meta.fields`.
- `src/apps/race/views.py` — `_validate_category_rows` (249), `_reconcile_categories` (359), контекст рядов категорий (478).
- `src/static/js/race_form.js`, `src/static/js/team-form.js`, `src/templates/race/race_form.html`.

Паттерны:

- Базис подсчёта занятости = `paid_people` (согласовано с `Race.people_count()`).
- `0 = без лимита` (unlimited) для обоих полей.
- Поля в `base-2` шаблонах рендерятся вручную (не `{{ form.field }}`).
- Категорийные ряды на race-форме передаются как `categories_json` и реконсилируются в одной транзакции (как `min_people`/`max_people`).

Зависимости: Django 4.2, DRF (api вне scope), VTB-оплата (точка триггера sold_out).

## Development Approach

- **testing approach**: Regular (код, затем тесты в рамках той же задачи).
- завершать каждую задачу полностью перед переходом к следующей; маленькие сфокусированные изменения.
- **КРИТИЧНО: каждая задача обязана включать новые/обновлённые тесты** для изменённого кода (success + error/edge сценарии), отдельными пунктами чек-листа.
- **КРИТИЧНО: все тесты должны проходить перед началом следующей задачи.**
- запускать `uv run pytest` после каждого изменения; `make format && make lint` перед коммитом.
- сохранять обратную совместимость (поле `default=0` ⇒ существующие гонки без лимита).

## Testing Strategy

- **unit tests**: pytest-style функции (`@pytest.mark.django_db`, fixtures `client`/`django_user_model`) в `src/website/tests.py`; wiring race-формы — в `src/apps/race/tests.py`.
- **e2e**: в проекте нет UI-e2e фреймворка (Playwright/Cypress) — пропускаем. Клиентский JS покрывается косвенно через серверную валидацию (source of truth).
- ключевая логика gate покрывается на уровне `TeamForm` (можно инстанцировать форму напрямую с `data`/`team`/`bypass_limits`).

## Progress Tracking

- отмечать `[x]` сразу по завершении пункта.
- новые задачи помечать ➕, блокеры — ⚠️.
- держать план в синхроне с реальной работой; обновлять при изменении scope.

## Solution Overview

- **Данные**: два `IntegerField` (`Race.people_limit`, `Category.people_limit`), `0 = unlimited`; хелперы `remaining_people()`/`people_count()` считают по `paid_people` (deleted исключены `TeamManager`).
- **Gate**: единая проверка в `TeamForm.clean()` после существующих size/maps-проверок. Race-check блокирует только рост состава (`needed = new_ucount − team.paid_people`), category-check блокирует вход в полную / рост в полной (с само-исключением через `exclude_team`). Суперюзер (`bypass_limits`) пропускает.
- **sold_out**: производный сигнал. Авто `OPEN → SOLD_OUT` в `update_team()` при достижении лимита гонки. Авто-реоткрытия нет (Option B).
- **Конфиг**: лимиты редактируются на race edit page (scalar `Race.people_limit` + per-row `Category.people_limit`).
- **UX/Display**: `data-remaining`/`raceRemaining` в team-форме (disable полных категорий, кап размера); бейджи «осталось N / мест нет».

Ключевое решение — gate в форме (а не во view): обе точки входа (`AddTeam`, `EditTeamView`) уже зовут `form.is_valid()`, переиспользование без дублирования. Известный компромисс paid-basis (параллельные неоплаченные черновики могут кратковременно превысить лимит) задокументирован как caveat, не чинится.

## Technical Details

**Хелперы** (`src/website/models/race.py`):

```python
# Category
def people_count(self):
    return Team.objects.filter(category2=self).aggregate(
        total=Sum("paid_people"))["total"] or 0

def remaining_people(self, exclude_team=None):
    if not self.people_limit:           # 0 → unlimited
        return None
    occupied = self.people_count()
    if exclude_team and exclude_team.category2_id == self.id:
        occupied -= exclude_team.paid_people
    return self.people_limit - occupied

# Race
def remaining_people(self):
    if not self.people_limit:
        return None
    return self.people_limit - self.people_count()
```

**Gate** (`TeamForm.clean()`, выполняется если `not bypass_limits`; `new_ucount`=submitted size, `dest`=submitted category):

```python
# Race — блокирует только рост
needed = new_ucount - team.paid_people
race_remaining = dest.race.remaining_people()
if race_remaining is not None and needed > 0 and needed > race_remaining:
    add_error("ucount", f"В гонке закончились места: осталось {race_remaining}.")

# Category — блокирует вход в полную / рост в полной
cat_remaining = dest.remaining_people(exclude_team=team)
moving_in = dest.id != team.category2_id
growing = new_ucount > team.paid_people
if cat_remaining is not None and (moving_in or growing) and new_ucount > cat_remaining:
    add_error("category2_id", f"В категории нет мест: осталось {cat_remaining}.")
```

На add: `team = Team()` (paid_people=0, category2_id=None) ⇒ `needed = new_ucount`, `moving_in=True`. `bypass_limits = request.user.is_superuser`.

**Триггер sold_out** (`update_team()` в `models.py`, сразу после `payment.team.save()`):

```python
race = payment.team.category2.race
if (race.people_limit and race.reg_status == RegStatus.OPEN
        and race.people_count() >= race.people_limit):
    race.reg_status = RegStatus.SOLD_OUT
    race.save(update_fields=["reg_status"])
```

**Caveat (документировать):** занятость считается по `paid_people`, который обновляется только при подтверждении оплаты ⇒ параллельные неоплаченные черновики могут кратковременно превысить лимит. Принято осознанно; не чинится.

## What Goes Where

- **Implementation Steps** (`[ ]`): модель, миграция, gate, триггер, конфиг-форма, JS/шаблоны, тесты.
- **Post-Completion** (без чекбоксов): ручная проверка реального VTB-оплатного flow на стейдже; возможный follow-up на `api`-app и `TeamFormAdmin`.

## Implementation Steps

### Task 1: Поля лимитов и хелперы occupancy

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0067_category_people_limit_race_people_limit.py`
- Modify: `src/website/tests.py`

- [x] добавить `Race.people_limit = IntegerField("Лимит участников", default=0)` (0 = без лимита)
- [x] добавить `Category.people_limit = IntegerField("Лимит участников", default=0)` (0 = без лимита; отличается от per-team `max_people`)
- [x] добавить `Category.people_count()` (Sum `paid_people`) и `Category.remaining_people(exclude_team=None)` с само-исключением
- [x] добавить `Race.remaining_people()` (через существующий `people_count()`)
- [x] сгенерировать миграцию: `uv run python src/manage.py makemigrations website`
- [x] тесты: `Category.people_count`/`remaining_people` (с `exclude_team`, исключение deleted), `Race.remaining_people` (limit=0 → None, limit>0 → корректная разница)
- [x] запустить тесты — должны пройти перед Task 2

### Task 2: Gate капасити в TeamForm + проброс из view

**Files:**
- Modify: `src/website/forms.py`
- Modify: `src/website/views/views_.py` (AddTeam)
- Modify: `src/website/views/team.py` (EditTeamView)
- Modify: `src/website/tests.py`

- [ ] расширить `TeamForm.__init__` параметрами `team=None` и `bypass_limits=False` (на add — `Team()`, paid_people=0)
- [ ] в `TeamForm.clean()` после существующих size/maps-проверок добавить race-check (блокирует только рост: `needed = new_ucount − team.paid_people`)
- [ ] добавить category-check (`moving_in or growing`, `remaining_people(exclude_team=team)`); пропуск всего gate при `bypass_limits`
- [ ] прокинуть `team` + `bypass_limits=request.user.is_superuser` в `TeamForm(...)` в `AddTeam.post` и `EditTeamView.post`
- [ ] тесты (ядро): race full → 2→3 рост блокируется; 2→2 переход в категорию-с-местом разрешён; category full → вход блокируется; edit-self без роста в полной категории разрешён; рост-в-полной блокируется; новая регистрация в полную гонку/категорию блокируется; `paid_people=0` черновики не занимают слот; `bypass_limits` пропускает; `people_limit=0` не ограничивает
- [ ] запустить тесты — должны пройти перед Task 3

### Task 3: Авто sold_out при подтверждении оплаты

**Files:**
- Modify: `src/website/models/models.py` (`update_team`)
- Modify: `src/website/tests.py`

- [ ] в `update_team()` сразу после `payment.team.save()` добавить блок флипа `OPEN → SOLD_OUT` (только если `people_limit` задан и достигнут)
- [ ] импортировать/использовать `RegStatus` без циклов импорта
- [ ] добавить комментарий-caveat про paid-basis рядом с триггером
- [ ] тесты: оплата, достигшая cap → `OPEN → SOLD_OUT`; удаление команды после НЕ реоткрывает (Option B); ручной `sold_out` ниже cap триггером не трогается; гонка без лимита не флипается
- [ ] запустить тесты — должны пройти перед Task 4

### Task 4: Конфиг лимитов на странице редактирования гонки

**Files:**
- Modify: `src/apps/race/forms.py` (`RaceForm.Meta.fields`)
- Modify: `src/templates/race/race_form.html`
- Modify: `src/apps/race/views.py` (`_validate_category_rows`, `_reconcile_categories`, контекст рядов)
- Modify: `src/static/js/race_form.js`
- Modify: `src/apps/race/tests.py`

- [ ] добавить `people_limit` в `RaceForm.Meta.fields` и ручной `<input type="number" min="0">` в `race_form.html` (base-2 convention, с выводом ошибок)
- [ ] парсить+валидировать `people_limit` в `_validate_category_rows` (неотрицательный int; пусто/0 = unlimited)
- [ ] сохранять `people_limit` в `_reconcile_categories`; добавить поле в контекст рядов категорий (views.py ~478)
- [ ] отрисовать number-input `people_limit` в ряду категории в `race_form.js`
- [ ] тесты (`apps/race/tests.py`): `RaceForm`/category-row принимает и сохраняет `people_limit`; негатив отклоняется; 0 принимается; round-trip edit гонки сохраняет лимиты
- [ ] запустить тесты — должны пройти перед Task 5

### Task 5: UX team-формы (disable полных категорий, кап размера)

**Files:**
- Modify: `src/website/views/views_.py` (`build_category_options`, `build_team_form_context`)
- Modify: `src/static/js/team-form.js`
- Modify: `src/website/tests.py`

- [ ] в `build_category_options` добавить `data-remaining` per `<option>` (пусто = unlimited)
- [ ] в `build_team_form_context`/`teamFormConfig` добавить `raceRemaining`
- [ ] `team-form.js`: disable `<option>` когда `remaining < min_people` (НО никогда не текущую категорию команды)
- [ ] `team-form.js`: кап segmented size-control до `min(category.max_people, что влезает по category remaining и race remaining)`
- [ ] тесты: `build_category_options` отдаёт корректный `data-remaining` (incl. unlimited и текущая категория не дизейблится); `build_team_form_context` содержит `raceRemaining`
- [ ] запустить тесты — должны пройти перед Task 6

### Task 6: Display бейджей «осталось N / мест нет»

**Files:**
- Modify: `src/templates/race/race_page.html` (и/или `src/templates/race/teams.html`)
- Modify: `src/apps/race/views.py` (контекст, если нужно)
- Modify: `src/apps/race/tests.py`

- [ ] показать «осталось N мест» / «мест нет» на race page через `race.remaining_people()` (скрывать при unlimited)
- [ ] показать per-category remaining на teams-list/race page (минимальный бейдж)
- [ ] тест: контекст/рендер содержит ожидаемые значения remaining для гонки с лимитом и без
- [ ] запустить тесты — должны пройти перед Task 7

### Task 7: Verify acceptance criteria

- [ ] проверить все требования из Overview (полная категория, переход без роста, запрет роста при полной гонке, авто sold_out)
- [ ] проверить edge cases (paid_people=0, deleted teams, supruser bypass, limit=0)
- [ ] полный прогон: `uv run pytest`
- [ ] `make format && make lint`

### Task 8: Документация и финализация

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/20260531-people-limits.md`

- [ ] обновить `CLAUDE.md`: новые поля `people_limit`, paid-basis занятости, gate в `TeamForm.clean`, авто sold_out (Option B), `0 = unlimited`
- [ ] перенести план в `docs/plans/completed/`

## Post-Completion

*Только информационные пункты — внешние действия/ручная проверка, без чекбоксов.*

**Manual verification:**
- Прогнать реальный VTB-оплатный flow на стейдже: убедиться, что `update_team()` флипает `sold_out` при достижении cap.
- Проверить параллельный сценарий: два пользователя одновременно регистрируются в последний слот (известный paid-basis caveat) — задокументировано, поведение ожидаемое.
- Визуальная проверка бейджей и disable-логики в браузере (mobile + desktop).

**External system updates / follow-ups (вне scope):**
- `api`-app team creation и `website.forms.TeamFormAdmin` НЕ применяют лимиты (суперюзеры обходят) — рассмотреть как отдельный follow-up при необходимости.
- Авто-реоткрытие при освобождении слотов сознательно не реализуется (Option B); при потребности — отдельная задача с разделением auto/manual `sold_out`.
