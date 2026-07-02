# Денормализованный снапшот-протокол результатов гонки

## Overview
Сейчас страница результатов (`/results/`) считается на лету из живых `Team`/`TakenKP`/`Checkpoint`
(`AllTeamsResultView`, `src/website/views/views_.py:486`). Любая правка команды или новое КП из
мобильного приложения немедленно меняет уже опубликованные результаты.

Вводим **денормализованный снапшот-протокол**: админ жмёт «Сформировать» — из живых данных
пересчитывается снапшот (`draft`, предварительный протокол, можно пересчитывать многократно);
жмёт «Зафиксировать» — `draft` замораживается в неизменяемый `final`. Публика видит только последний
`final`; админ видит последний снапшот любого статуса. Страница `/results/` читает **только** строки
снапшота, не касаясь живых моделей — это гарантия неизменности. Старая вьюха переезжает на
`/results-deprecated/`.

Ключевая выгода: результаты, показанные участникам, не «плывут» от поздних правок и до-загрузки КП.
Побочно — штраф считается из `Category.control_time`/`overtime_penalty` (поля из PR #234) вместо
захардкоженных id категорий 16–23.

## Context (from discovery)
- **Стек**: Django 4.2, исходники под `src/`, pytest-стиль тестов (`@pytest.mark.django_db`, фикстуры
  `client`/`django_user_model`), `DJANGO_SETTINGS_MODULE=config.settings`.
- **Новая функциональность живёт в `apps.race`** (`label = "race_app"`), т.к. `website` — deprecated.
  `apps.race` уже держит кросс-апповые FK в `website` (`RaceExtra`/`TeamExtra`/`PaymentExtra`) и имеет
  собственные миграции (`0001_initial`, `0002_migrate_maps_to_extras`), первая объявляет
  `dependencies = [("website", "0072_payment_vtb_payment")]`.
- **Файлы `apps.race`**: `models.py`, `views.py`, `forms.py`, `permissions.py`, `pricing.py`,
  `tests.py`, `migrations/`. URL-роутинг вьюх `apps.race` живёт в `src/website/urls.py`
  (пример: `RaceEditView`, `RaceTeamsView` импортируются туда).
- **`can_edit_race(user, race)`** (`src/apps/race/permissions.py`): superuser или
  `RaceAdmin(role=ADMIN)`. Тот же гейт, что у `RaceEditView`/`RaceLegendEditView`.
- **Старая логика результатов** (`AllTeamsResultView`, `src/website/views/views_.py:486-625`):
  оплаченные стартовавшие команды (`paid_people__gt=0`, `exclude(start_time=0)`,
  `category2__race_id=race`); дедуп КП по номеру для NFC (`TakenKP.exclude(nfc="")`) и фото
  (`exclude(image_url="")`); подсчёт уникальных чипов (split `TakenKP.nfc` по `,`); стоимость КП из
  `Checkpoint.objects.filter(race_id=..., cost__gte=0)` (словарь `number → cost`); штраф по
  захардкоженным id (16–23) — **заменяем на `Category.control_time`/`overtime_penalty`**; сортировка
  внутри категории по `(-summ_after_penalty, time_diff)`, места по порядку.
- **Модели**:
  - `Team` (`src/website/models/models.py:146`): `paid_people`, `start_time`/`finish_time` (bigint ms),
    `ucount`, `teamname`, `start_number`, `city`, `organization`, `category2` (FK `Category`),
    `athlet1..athlet6`, `dnf`, менеджер `objects` уже исключает `is_deleted`.
  - `TakenKP` (`src/website/models/models.py:458`): `team` (FK), `point_number`, `image_url`,
    `timestamp`, `nfc`.
  - `Checkpoint` (`src/website/models/checkpoint.py:7`): `race` (FK), `number`, `cost`.
  - `Category` (`src/website/models/race.py:240`): `code`, `short_name`, `name`, `race` (FK),
    `control_time` (мин, 0 = не задано), `overtime_penalty` (баллов/мин просрочки, 0 = без штрафа).
- **URL сейчас** (`src/website/urls.py`): `race/<slug:race_slug>/category/<int:category_id>/results/`
  → `views.AllTeamsResultView` имя `category_results`. Есть int-редирект-заглушка
  `race/<int:race_id>/category/<int:category_id>/results/` (`RaceIdRedirectView`).

## Development Approach
- **testing approach**: Regular (код, затем тесты) — как принято в проекте.
- каждую задачу доводим до конца до перехода к следующей; маленькие фокусные изменения.
- **CRITICAL: каждая задача с изменением кода ОБЯЗАНА включать новые/обновлённые тесты** — отдельными
  пунктами чек-листа, успешные + краевые сценарии.
- **CRITICAL: все тесты зелёные перед началом следующей задачи.**
- запускать `uv run pytest --reuse-db` после изменений; `make format && make lint` перед коммитом.
- обратная совместимость: старая вьюха остаётся, только меняет URL.

## Testing Strategy
- **unit/integration тесты**: pytest-стиль в `src/apps/race/tests.py` для каждой задачи.
- **e2e**: UI-e2e в проекте нет — покрываем через Django test client (GET/POST вьюх, проверка контекста
  и HTTP-кодов).
- каждый тест-набор — часть той же задачи, где меняется код; должен проходить до следующей задачи.

## Progress Tracking
- отмечать `[x]` сразу по завершении пункта.
- новые задачи — с префиксом ➕, блокеры — ⚠️.
- держать план синхронным с фактической работой; при отклонении от scope — править план.

## Solution Overview
- **Две модели** в `apps.race`: `Protocol` (версия протокола гонки) и `ProtocolRow` (денормализованная
  строка команды). Отображение читает только `ProtocolRow`, живые модели не трогает.
- **Сервис** `src/apps/race/results.py`: `build_protocol(race, user)` (пересчёт draft на месте или
  новый draft), `freeze_protocol(race)` (draft→final). Вся «грязная» логика с живыми моделями — здесь.
- **Вьюхи** в `apps.race`: `ProtocolView` (GET, выбор протокола по правам), `build`/`freeze` (POST, гейт
  `can_edit_race`). Старая `AllTeamsResultView` остаётся в `website`, переезжает на
  `/results-deprecated/`.
- **Правило видимости**: `can_edit_race` → последний `Protocol` (любой статус); иначе → последний
  `final`; нет подходящего → «Протокол ещё не опубликован».

## Technical Details

### Модель `Protocol`
- `race = ForeignKey("website.Race", on_delete=CASCADE, related_name="protocols")`
- `status = CharField(choices=[("draft","draft"),("final","final")], default="draft")` — константы
  `Protocol.DRAFT`/`Protocol.FINAL`.
- `created_at = DateTimeField(auto_now_add=True)`
- `frozen_at = DateTimeField(null=True, blank=True)`
- `created_by = ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL)`
- `Meta.ordering = ["-created_at"]`; хелпер-менеджер/метод для «последнего протокола гонки».

### Модель `ProtocolRow`
- `protocol = ForeignKey(Protocol, on_delete=CASCADE, related_name="rows")`
- Идентификация: `place` (int), `team_id` (int, ссылка), `start_number` (char), `team_name` (char),
  `members` (char/text), `club` (char, бывш. organization — в UI «Клуб»), `city` (char),
  `member_count` (int, бывш. ucount).
- Категория (копия на момент снапшота): `category_id` (int), `category_code` (char),
  `category_name` (char), `category_short_name` (char).
- NFC: `nfc_checkpoints` (char/text, «5, 12, 18»), `nfc_count` (int), `nfc_score` (int).
- Фото: `photo_checkpoints` (char/text), `photo_count` (int), `photo_score` (int).
- Итоги баллов: `chips_count` (int, бывш. unique_nfc_tags_count), `total_score` (int,
  = `nfc_score + photo_score`).
- Время: `start_time_ms` (bigint), `finish_time_ms` (bigint), `duration_ms` (bigint),
  `duration_str` (char, «5:23:10» или «-»).
- Результат: `penalty` (int, баллы), `final_score` (int, = `total_score - penalty`), `dnf` (bool).
- Индекс на `(protocol, category_id)` для фильтра страницы.

### Сервис `build_protocol(race, user) -> Protocol`
1. `transaction.atomic()`:
   - **Лок на строку гонки, а не на queryset протоколов**:
     `Race.objects.select_for_update().get(id=race.id)` в начале блока. `select_for_update` на пустом
     `Protocol`-queryset (первый build, протоколов ещё нет) ничего не блокирует → два конкурентных
     первых build создали бы два draft. Лок родительской `Race` сериализует критическую секцию даже
     когда протоколов нет.
   - `latest = Protocol.objects.filter(race=race).order_by("-created_at").first()`.
   - если `latest and latest.status == DRAFT` → `protocol = latest`, `protocol.rows.all().delete()`.
   - иначе → `protocol = Protocol.objects.create(race=race, status=DRAFT, created_by=user)`.
2. Собрать данные (порт логики `AllTeamsResultView`, семантика баллов сохранена):
   - команды: `Team.objects.filter(category2__race_id=race.id, paid_people__gt=0)
     .exclude(start_time=0).select_related("category2")`.
   - `cost = {cp.number: cp.cost}` из `Checkpoint.objects.filter(race_id=race.id, cost__gte=0)`.
   - на команду: `members` (склейка `athlet1..athletN` по `ucount`); NFC — упорядоченные по
     `timestamp` `TakenKP.exclude(nfc="")`, дедуп по `point_number`, `nfc_score`/`nfc_count`/список;
     `chips_count` = число уникальных значений после split `nfc` по `,`; фото — `TakenKP.exclude(
     image_url="").distinct("point_number")` (DISTINCT ON — Postgres-only, как в старой вьюхе; тест-БД
     Postgres), учитывать только КП не из NFC-набора; `total_score = nfc_score + photo_score`;
     `duration_ms = finish - start` (0 если нет финиша), `duration_str`.
   - ⚠️ **`cost.get(point.point_number)`, НЕ `cost[...]`**: старая вьюха обращается к словарю прямым
     индексом (`views_.py:536,565`) → `KeyError` на КП без строки `Checkpoint` (или с `cost<0`,
     отфильтрованным `cost__gte=0`). В старой вьюхе это било только по одной просматриваемой категории;
     `build_protocol` проходит **всю гонку в одной транзакции**, поэтому один осиротевший
     `point_number` где угодно уронил бы весь build (500 + rollback) — ничего не опубликуется.
     Использовать `cost.get(...)` и пропускать неизвестные номера.
   - **штраф из категории**: `control = category.control_time`; если `control > 0` и
     `duration_min > control` → `overtime_min = duration_min - control`,
     `penalty = overtime_min * category.overtime_penalty`; иначе `penalty = 0`.
     `final_score = total_score - penalty`.
3. Сортировка и места — **группировка по `category_id`** (сброс `place` при смене `category_id`),
   внутри группы по `(-final_score, duration_ms)`. ⚠️ **Сознательное отличие от старой вьюхи**: та
   группирует по deprecated `Team.category` (CharField, с нормализацией `"6h"→"06h"`); мы группируем по
   `category_id`. Это согласовано с фильтром отображения (`rows.filter(category_id=...)`); для гонок с
   расхождением `category`/`category2` порядок/места могут отличаться от `/results-deprecated/` — это
   ожидаемо.
4. `ProtocolRow.objects.bulk_create([...])`.
5. вернуть `protocol`.

> **Зависимость по данным (штраф):** старая формула — 1 балл за минуту просрочки при захардкоженных
> контрольных временах (6/8/12/24 ч по id категорий). Новая — `overtime_min * overtime_penalty` при
> `control_time > 0`. Оба поля `Category` по умолчанию `0`, поэтому **до заполнения `control_time`/
> `overtime_penalty` у категорий гонки штраф молча = 0**. Перед `freeze` реального протокола убедиться,
> что поля категорий заполнены (см. Post-Completion), иначе в `final` зафиксируются результаты без
> штрафов.

### Сервис `freeze_protocol(race) -> Protocol | None`
- `transaction.atomic()` + `Race.objects.select_for_update().get(id=race.id)` (лок на ту же строку
  `Race`, что и `build_protocol` — чтобы конкурентные build+freeze на одной гонке сериализовались на
  общем ресурсе); последний протокол; если `status == DRAFT` → `status = FINAL`,
  `frozen_at = timezone.now()`, сохранить, вернуть; иначе — `None` (no-op).

### Вьюхи (`src/apps/race/views.py`)
- `ProtocolView(View).get(request, race_slug, category_id)`:
  - `race = get_object_or_404(Race, slug=race_slug)`.
  - `can_edit = can_edit_race(request.user, race)`.
  - выбор: `qs = race.protocols.all()`; если не `can_edit` → `qs.filter(status=FINAL)`;
    `protocol = qs.order_by("-created_at").first()`.
  - если нет → рендер с сообщением «Протокол ещё не опубликован» (+ для админа кнопка «Сформировать»).
  - иначе → `rows = protocol.rows.filter(category_id=category_id).order_by("place")`; заголовок по
    статусу (`draft`→«Предварительный протокол», `final`→«Итоговый протокол»); контекст `can_edit`,
    `race`, `category` (из `Category` по `category_id`, только для шапки).
- `ProtocolBuildView(View).post(...)` и `ProtocolFreezeView(View).post(...)`: гейт `can_edit_race`
  (иначе 403), вызвать сервис, `messages`-уведомление, redirect назад (`HTTP_REFERER` или
  `category_results` первой категории гонки).
- Старая `AllTeamsResultView` — без изменений в `website`, только новый URL.

### Шаблон `src/templates/race/protocol.html`
- Переработать `teams_result.html` под новые имена полей `ProtocolRow`; убрать закомментированные блоки
  (mock dropdown/modal), сохранить print-landscape `@media print { @page { size: landscape } }`.
- Для `can_edit` — форма-кнопки «Сформировать» (POST build) и «Зафиксировать» (POST freeze) c CSRF.
- Подсветка «Чипов: N» при `member_count != chips_count and chips_count != 0`.

### URL (`src/website/urls.py`)
- `race/<slug:race_slug>/category/<int:category_id>/results/` → `RaceProtocolView` имя
  `category_results` (перенаправить на новую вьюху `apps.race`).
- `race/<slug:race_slug>/category/<int:category_id>/results-deprecated/` → `AllTeamsResultView` имя
  `category_results_deprecated`.
- `race/<slug:race_slug>/results/build/` → `ProtocolBuildView` имя `protocol_build` (POST).
- `race/<slug:race_slug>/results/freeze/` → `ProtocolFreezeView` имя `protocol_freeze` (POST).

## What Goes Where
- **Implementation Steps** (`[ ]`): модели+миграция, сервис, вьюхи, шаблон, URL, тесты — всё в репозитории.
- **Post-Completion** (без чекбоксов): ручная проверка UX на реальной гонке, прогон миграции на проде.

## Implementation Steps

### Task 1: Модели `Protocol` и `ProtocolRow` + миграция

**Files:**
- Modify: `src/apps/race/models.py`
- Create: `src/apps/race/migrations/0003_protocol_protocolrow.py` (через `makemigrations`)
- Modify: `src/apps/race/tests.py`

- [ ] добавить в `models.py` модель `Protocol` (поля/константы/`Meta.ordering` из Technical Details),
      FK `race → "website.Race"` CASCADE `related_name="protocols"`, `created_by → AUTH_USER_MODEL`
      SET_NULL null.
- [ ] добавить модель `ProtocolRow` со всеми денормализованными полями и индексом `(protocol,
      category_id)`; `__str__` для читаемости в админке.
- [ ] сгенерировать миграцию: `uv run python src/manage.py makemigrations race_app`
      (⚠️ app label — `race_app`, НЕ `race`; иначе «No installed app with label 'race'». Проверить, что
      файл в `apps/race/migrations/` и зависит от `website`).
- [ ] написать тесты: создание `Protocol`/`ProtocolRow`, `related_name` (`race.protocols`,
      `protocol.rows`), дефолт `status=draft`, CASCADE-удаление строк при удалении протокола.
- [ ] написать тест краевого случая: `frozen_at`/`created_by` допускают `null`.
- [ ] `uv run pytest src/apps/race/tests.py --reuse-db` — зелёные перед Task 2.

### Task 2: Сервис `build_protocol` / `freeze_protocol`

**Files:**
- Create: `src/apps/race/results.py`
- Modify: `src/apps/race/tests.py`

- [ ] реализовать `build_protocol(race, user)`: выбор/создание draft под `select_for_update` в
      `transaction.atomic()`, порт логики результатов, штраф из `Category.control_time`/
      `overtime_penalty`, сортировка+места по категориям, `bulk_create` строк.
- [ ] вынести хелперы подсчёта (members-склейка, NFC/фото-дедуп, `duration_str`) в приватные функции
      модуля для читаемости и тестируемости.
- [ ] реализовать `freeze_protocol(race)`: draft→final с `frozen_at`, no-op (возврат `None`) без draft.
- [ ] тест: `build_protocol` создаёт корректные строки — баллы NFC/фото, `total_score`, места по
      `(-final_score, duration_ms)`, `chips_count`.
- [ ] тест штрафа из категории: `control_time>0` и просрочка → `penalty = overtime_min *
      overtime_penalty`, `final_score = total_score - penalty`; `control_time=0` → `penalty=0`.
- [ ] тест паритета магнитуды со старой формулой: категория с `overtime_penalty=1` даёт `penalty ==
      overtime_min` (1 балл/мин — как в старой вьюхе).
- [ ] тест осиротевшего КП: `TakenKP.point_number`, которого нет в `cost`-словаре (нет `Checkpoint`
      или `cost<0`) → build **не падает**, неизвестный номер пропущен (проверка `cost.get`, не `[]`).
- [ ] тест жизненного цикла: повторный `build` при draft пересчитывает **тот же** протокол (rows
      переписаны, не накапливаются); после `freeze` следующий `build` создаёт **новый** draft, а final
      остаётся неизменным.
- [ ] тест краевого случая: гонка без стартовавших команд → протокол с 0 строк; `freeze` без draft →
      `None`, статус final не трогается.
- [ ] `uv run pytest src/apps/race/tests.py --reuse-db` — зелёные перед Task 3.

### Task 3: Вьюха отображения `ProtocolView` + шаблон

**Files:**
- Modify: `src/apps/race/views.py`
- Create: `src/templates/race/protocol.html`
- Modify: `src/apps/race/tests.py`

- [ ] `ProtocolView.get`: выбор протокола по правам (`can_edit_race` → последний любой; иначе последний
      `final`; нет → сообщение), строки `filter(category_id=...)`, заголовок по статусу, контекст.
- [ ] создать `src/templates/race/protocol.html` на базе `teams_result.html` под новые имена полей,
      без mock-блоков, с print-landscape и подсветкой расхождения чипов.
- [ ] тест видимости: публика (аноним/не-админ) видит только `final` (при наличии только draft →
      «Протокол ещё не опубликован»); админ видит draft.
- [ ] тест: страница фильтрует строки по `category_id`; заголовок соответствует статусу.
- [ ] тест **гарантии неизменности**: сформировали протокол → изменили `Team.teamname` и добавили
      `TakenKP` → GET `/results/` возвращает прежние значения (снапшот не изменился до пересчёта).
- [ ] `uv run pytest src/apps/race/tests.py --reuse-db` — зелёные перед Task 4.

### Task 4: POST-вьюхи build/freeze + кнопки

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/protocol.html`
- Modify: `src/apps/race/tests.py`

- [ ] `ProtocolBuildView.post` и `ProtocolFreezeView.post`: гейт `can_edit_race` (403 иначе), вызов
      сервиса, `messages`, redirect назад.
- [ ] добавить в шаблон формы-кнопки «Сформировать»/«Зафиксировать» c CSRF, видимые только при
      `can_edit`.
- [ ] тест: POST build/freeze не-админом → 403, протокол не создан/не изменён.
- [ ] тест: POST build админом создаёт draft; POST freeze переводит в final; повторный build после
      freeze создаёт новый draft.
- [ ] тест: freeze без draft — дружелюбное сообщение, без 500.
- [ ] `uv run pytest src/apps/race/tests.py --reuse-db` — зелёные перед Task 5.

### Task 5: URL-роутинг (новый /results/ + /results-deprecated/)

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] импортировать вьюхи `apps.race` в `website/urls.py`; переключить `category_results` на
      `ProtocolView`.
- [ ] добавить `results-deprecated/` → `AllTeamsResultView` имя `category_results_deprecated`.
- [ ] добавить `results/build/` (`protocol_build`) и `results/freeze/` (`protocol_freeze`).
- [ ] тест: депрекейтнутый URL рендерит старую вьюху (`teams_result.html`), новый `category_results`
      рендерит снапшот-шаблон; `reverse()` имён резолвится.
- [ ] тест: старый int-URL results (`RaceIdRedirectView`) по-прежнему редиректит (редирект **path-based**
      — строковая замена `/race/<id>/`→`/race/<slug>/`, не `reverse("category_results")`, поэтому смена
      вьюхи за именем его не затрагивает; тест как страховка).
- [ ] `uv run pytest src/apps/race/tests.py --reuse-db` — зелёные перед Task 6.

### Task 6: Verify acceptance criteria
- [ ] проверить все требования Overview: снапшот неизменен к правкам живых данных; штраф из полей
      `Category`; видимость draft/final; старый URL на `/results-deprecated/`.
- [ ] проверить краевые случаи: пустая гонка, `control_time=0`, freeze без draft, осиротевший
      `point_number`, **два конкурентных первых build** (лок на `Race` → один draft, не два).
- [ ] полный прогон: `uv run pytest`.
- [ ] `make lint` — чисто.

### Task 7: [Final] Документация и оформление
- [ ] обновить `CLAUDE.md`: краткая заметка про `apps.race` `Protocol`/`ProtocolRow`, сервис
      `results.py`, URL `results`/`results-deprecated`/`build`/`freeze`, правило видимости.
- [ ] `make format && make lint` перед коммитом.
- [ ] переместить план в `docs/plans/completed/`.

## Post-Completion
*Требуют ручного действия или внешних систем — без чекбоксов, информационно.*

**Manual verification:**
- ⚠️ **перед первым `freeze` реального протокола** — проверить, что у категорий гонки заполнены
  `control_time`/`overtime_penalty` (оба по умолчанию `0` → штраф молча = 0). Иначе в `final`
  зафиксируются результаты без штрафов.
- прогнать сценарий на staging: сформировать → проверить предварительный → изменить команду/добавить КП
  → убедиться что публичный `/results/` не изменился → зафиксировать → проверить итоговый.
- визуально проверить печать протокола (landscape) и мобильную вёрстку таблицы.

**External system updates:**
- применить миграцию `apps.race` на проде (`manage.py migrate`) при деплое.
- заметить в анонсе/для судей смену адреса старого протокола на `/results-deprecated/` (редиректов нет).
