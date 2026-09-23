# Эндпоинт привязки браслета участника и выдачи кода

## Overview

- Мобильное приложение (iOS, план `kolco24_ios/docs/plans/completed/20260923-member-chip-provisioning.md`)
  записывает на браслет участника серверный секретный код (формат `K24`, тип `0x2`) — по аналогии с
  провижинингом КП. Сейчас браслет опознаётся только по NFC UID, который легко подделать.
- Нужен эндпоинт, который по UID браслета отдаёт код (UID уже в пуле) или создаёт привязку UID → номер
  и отдаёт код (новый браслет).
- Встраивается в `apps.mobile` рядом с `POST /app/race/<id>/tags/` (провижининг КП): тот же стек прав,
  тот же троттлинг, та же дисциплина идемпотентности.

## Context (from discovery)

- `src/website/models/tag.py` — `Tag(number, nfc_uid unique, last_seen_at, updated_at)`, пул **глобальный**
  (нет FK на гонку), `save()` нормализует `nfc_uid`. Поля `code` нет.
- `src/apps/mobile/views.py:224` — `TagCreateView`: образец (права, троттлинг, `IntegrityError` → перечитать,
  ремонт кода под `select_for_update` с перепроверкой).
- `src/apps/mobile/views.py:933` — `MemberTagsView` (GET пула) — **не трогаем**.
- `src/apps/mobile/serializers.py` — `TagCreateSerializer` (образец), `MemberTagSerializer` (поля
  `number`, `nfc_uid`).
- `src/apps/mobile/versioning.py` — `member_tags_version` хеширует `(id, number, nfc_uid)`; код в fingerprint
  не входит.
- `src/api/serializers/tag.py` — `TagSerializer` с явным `fields` (код не утечёт).
- `src/website/admin.py:324` — `TagAdmin` (`list_display` без кода; `BinaryField` по умолчанию
  `editable=False`, в форму не попадёт).
- `src/apps/mobile/tests.py` — хелперы `_signed_post`, `_signed_post_auth`, `_make_active_token`,
  `_make_admin_race`, autouse `_clear_throttle_cache`; тесты `test_tag_create_*` — образец.
- Последняя миграция `website`: `0096_backfill_legacy_refunds`.

## Development Approach

- **testing approach**: Regular (код, затем тесты в той же задаче)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- maintain backward compatibility (GET `member_tags`, `api` `/api/member_tag/` и ETag ведут себя как раньше)
- работа в ветке (не `master`); `make format && make lint` перед каждым коммитом

## Testing Strategy

- **unit tests**: pytest-функции с `@pytest.mark.django_db` в `src/apps/mobile/tests.py`, реальная БД,
  подписанные запросы через существующие хелперы.
- **e2e tests**: в проекте нет.
- Команды: `uv run pytest src/apps/mobile/tests.py --reuse-db -k member_tag_bind`, полный — `uv run pytest`.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

- **Путь** `POST /app/race/<race_id>/member_tags/bind/`, URL name `member_tag_bind`. Сознательное отклонение
  от iOS-контракта (`member_tags/`): отдельный путь даёт отдельный view с правами в атрибутах класса, без
  ветвления `get_permissions()` по методу рядом с GET `MemberTagsView`. iOS меняет одну строку пути.
- **Отдельный view** `MemberTagBindView(AppAPIView)` по образцу `TagCreateView`:
  `permission_classes = [SignedAppPermission, IsMobileUser, CanEditRaceLegend]` (админ гонки из URL —
  `RaceAdmin(role=ADMIN)` через `can_edit_race`), `ClientIPScopedRateThrottle`, scope `mobile-write`.
  Пул глобальный, поэтому админ любой гонки может привязывать браслеты — принято.
- **Код** — новое поле `Tag.code` (16 случайных байт), выпускается **лениво** в эндпоинте под
  `select_for_update()` с перепроверкой: старые браслеты получают код при первом POST, два параллельных
  запроса не выпустят два разных кода. Выпущенный код не меняется никогда.
- **Не делаем** (YAGNI): `created_by`, `bid`, backfill кодов, выдача кода в GET, использование кода в
  отметках/судейских сканах.

## Technical Details

### Модель

`Tag.code = models.BinaryField("Код браслета", null=True, blank=True)` — миграция
`website/0097_tag_code` (только схема). В `TagAdmin` код не показывается.

### Запрос

```json
{"nfc_uid": "04A1B2C3D4E5F6", "number": 101}
{"nfc_uid": "04A1B2C3D4E5F6", "number": null}
```

`MemberTagBindSerializer`:
- `nfc_uid = CharField(allow_blank=False, trim_whitespace=True, max_length=255)`;
- `number = IntegerField(allow_null=True, min_value=1, max_value=2147483647)` — ключ обязателен
  (клиент всегда шлёт его, `null` явно), отсутствие → 400.

### Ответы

| Статус | Когда |
|---|---|
| `201` | UID неизвестен, `number` задан → создан `Tag` |
| `200` | UID известен и `number` `null` или совпадает (идемпотентно, тот же код) |
| `404` | UID неизвестен и `number == null` (`{"detail": "Браслет не найден"}`); или гонка не опубликована |
| `409` | UID привязан к **другому** номеру (`{"detail": "Браслет уже привязан к другому участнику"}`) |
| `400` | невалидное тело |
| `401` / `403` / `429` | нет/плохой Bearer / нейтральный отказ подписи или не-админ / троттлинг |

Тело 200/201: `{"number": tag.number, "nfc_uid": tag.nfc_uid, "code": "<32 hex>"}` — `number` из БД
(при `number: null` клиент узнаёт номер из ответа).

`Tag.number` не уникален: новый UID с уже занятым номером → `201` (запасной браслет).

### Поток `post(request, race_id)`

1. `get_object_or_404(Race, pk=race_id, is_published=True)`.
2. Валидация → 400.
3. `nfc_uid = ….strip().upper()`, `tag = Tag.objects.filter(nfc_uid=nfc_uid).first()`.
4. `tag is None`:
   - `number is None` → 404;
   - иначе `Tag(number=…, nfc_uid=…, code=os.urandom(16)).save()` в `transaction.atomic()` — код
     выпускается **сразу при вставке** (ленивый путь только для старых строк);
     `except IntegrityError as exc` (параллельное создание, глобальный `unique`) →
     `tag = Tag.objects.filter(nfc_uid=…).first()`; `None` → `raise exc` (не прятать другую ошибку БД
     за `DoesNotExist`, как в `TagCreateView`) → иначе `_resolve_existing(tag, number)`;
   - `_response(tag, 201)` вызывается **после** блока `atomic()`, вне `try`.
5. `_resolve_existing(tag, number)`: `number is not None and tag.number != number` → 409, иначе
   `_response(tag, 200)`.
6. `_response(tag, status)`: если `tag.code is None` → `atomic` +
   `tag = Tag.objects.select_for_update().get(pk=tag.pk)` (**переприсвоить** `tag` строке под локом) →
   если `tag.code is None` → `tag.code = os.urandom(16)`, `save(update_fields=["code", "updated_at"])`.
   Ответ строится из этого же `tag`: `bytes(tag.code).hex()` (`BinaryField` читается как `memoryview`).
   Паттерн — `TagCreateView._tag_response`.

### Инварианты

- GET `member_tags` (`MemberTagSerializer`) и `api` `TagSerializer` — явные поля, `code` не отдают.
- `member_tags_version`: создание `Tag` сдвигает fingerprint (новый кортеж), выпуск кода — нет
  (`code` не в хеше). `update_fields` включает `"updated_at"` по общей дисциплине.
- Код привязан к строке `Tag`, не к UID: если организатор правит `nfc_uid` в `/admin/` (замена
  браслета), старый код переходит на новый UID. Принято, фиксируется в документации.
- `mobile-write` 60/min — общий IP-бакет с `/tags/`, `/track/`, `/marks/`, `/judge_scans/`; за одним NAT
  станция провижининга делит лимит с загрузками треков. Существующее поведение, отдельный scope не вводим.

## What Goes Where

- **Implementation Steps**: модель, миграция, serializer, view, URL, тесты, CLAUDE.md, README.
- **Post-Completion**: правка пути в iOS, деплой, ручная проверка на устройстве.

## Implementation Steps

### Task 1: Поле `Tag.code` и миграция

**Files:**
- Modify: `src/website/models/tag.py`
- Create: `src/website/migrations/0097_tag_code.py`
- Modify: `src/apps/mobile/tests.py`

- [x] добавить `code = models.BinaryField("Код браслета", null=True, blank=True)` в `Tag`
- [x] `uv run python src/manage.py makemigrations website -n tag_code` → `0097_tag_code`
- [x] `TagAdmin` не меняется (`list_display` без `code`)
- [x] тест: `Tag` с `code` → GET `/app/race/<id>/member_tags/` не содержит ключа `code`
- [x] тест: `Tag` с `code` → `/api/member_tag/` не содержит ключа `code`
- [x] прогнать тесты — зелёные

### Task 2: Serializer `MemberTagBindSerializer`

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [x] `MemberTagBindSerializer` (`nfc_uid`, `number` — см. Technical Details) с docstring по образцу `TagCreateSerializer`
- [x] тесты: валидное тело с номером и с `null`
- [x] тесты: нет ключа `number`, нет ключа `nfc_uid`, `number: 0`, `number: 2**31`, `number: "abc"`, `number: 1.5`, пустой `nfc_uid`, `nfc_uid` длиннее 255 → невалидно
- [x] прогнать тесты — зелёные

### Task 3: `MemberTagBindView` и URL

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] `MemberTagBindView(AppAPIView)`: права, троттлинг, `post`, `_resolve_existing`, `_response` (поток из Technical Details), docstring по образцу `TagCreateView`
- [ ] URL `race/<int:race_id>/member_tags/bind/`, name `member_tag_bind`
- [ ] тест: неизвестный UID + номер → 201, `Tag` создан, `code` 16 байт, ответ `{number, nfc_uid, code}` с hex-кодом из БД
- [ ] тест: UID нормализуется (` 04ab ` → `04AB`)
- [ ] тест: повтор с тем же номером → 200, тот же код; повтор с `number: null` → 200, номер из БД
- [ ] тест: неизвестный UID + `null` → 404, ничего не создано
- [ ] тест: UID старого `Tag` без кода привязан к другому номеру → 409, `code` остался `None`, `updated_at` не изменился
- [ ] тест: старый `Tag` без кода → 200, код выпущен и сохранён; второй запрос отдаёт тот же код
- [ ] тест: выпуск кода старому `Tag` через POST не меняет ETag `/member_tags/` и `versions.member_tags` в `sync`
- [ ] тест: устаревший экземпляр — `_response` получает `Tag` с `code=None`, а в БД код уже есть → в ответе код из БД, перезаписи нет
- [ ] тест: новый UID с уже занятым номером → 201 (два `Tag` с одним номером)
- [ ] тест: `IntegrityError` при создании — `Tag` заранее в БД, первичный поиск во view (вынести в маленький хелпер) патчится в `None`; POST с тем же номером → 200 с кодом, с другим → 409 без выпуска кода
- [ ] тест: `IntegrityError` без строки в БД (patch `Tag.save`) → исходная ошибка пробрасывается (`client.raise_request_exception`/`pytest.raises`)
- [ ] тесты прав: без подписи → нейтральный 403 `{"detail": "Forbidden"}`; без Bearer / отозванный токен → 401; не-админ гонки → 403; неопубликованная гонка → 404
- [ ] тест: 400 на невалидном теле через эндпоинт (нет `number`)
- [ ] тест: создание сдвигает ETag `member_tags`
- [ ] тест: подписанный GET с Bearer админа на `/bind/` → 405 (без полной авторизации DRF отдаёт 401/403 раньше 405)
- [ ] прогнать тесты — зелёные

### Task 4: Verify acceptance criteria

- [ ] все строки таблицы ответов реализованы и покрыты тестами
- [ ] GET `member_tags` и `/api/member_tag/` не изменились (существующие тесты зелёные)
- [ ] полный suite: `uv run pytest`
- [ ] `make format && make lint`

### Task 5: [Final] Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `src/apps/mobile/README.md`

- [ ] CLAUDE.md, блок `apps.mobile`: короткий пункт **Member-tag bind** (путь, стек прав, таблица 201/200/404/409, код при вставке + ленивый под lock для старых строк, «`code` не отдаётся ни в одном GET», ETag двигается от создания, не от выдачи кода, код следует за строкой при правке `nfc_uid`); «седьмой POST» — в нумерации, где `judge_scans` шестой
- [ ] `src/apps/mobile/README.md`: строка в таблице эндпоинтов и в схеме прав рядом с `tags/`; заметка про общий бакет `mobile-write`
- [ ] переместить план в `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems - no checkboxes, informational only*

**iOS (`~/src/kolco24_ios`, отдельная задача):**
- путь в `ApiClient.bindMemberTag` → `/app/race/<id>/member_tags/bind/` + ожидаемый путь в `ApiClientTests`

**Перед деплоем:**
- проверить прод: `Tag.objects.filter(number__lte=0).count()` — такие браслеты адресуются только с
  `number: null` (`min_value=1`)

**Ручная проверка на устройстве (после деплоя):**
- браслет из пула: тап → тап → «№N записан»; «Проверить чип участника» → «код записан»
- новый браслет: тап → ввод номера → «Привязать» → тап → успех
- браслет, привязанный к другому номеру → текст про 409
