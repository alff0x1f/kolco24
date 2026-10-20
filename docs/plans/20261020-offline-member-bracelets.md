# Офлайн-выдача браслетов участников: сервер (K24 v2, HMAC-код гонки)

## Overview

- Сейчас браслет участника привязывается только онлайн: `POST /app/race/<id>/member_tags/bind/` выдаёт
  случайный 16-байтный `code`, а приложение участника узнаёт номер по UID из скачанного пула `member_tags`.
  Если заготовленные браслеты закончились, а интернета нет, выдать новый браслет нельзя.
- Новый формат браслета **v2**: номер и `race_id` лежат на чипе открыто, `code` судейский телефон
  считает сам — `HMAC-SHA256(K_race, uid ‖ race_id ‖ number)[:12]`. Ключ гонки `K_race` судья скачивает
  заранее. Сервер проверяет `code` при загрузке отметок и помечает несовпадения.
- Защищаемся от записи участником «я №N» на пустую метку и от переноса записи на метку с другим UID.
  Клон с тем же UID (magic-метки) — вне рамок. Приложение участника ключа не знает и код не проверяет;
  подмена выясняется на сервере (на финише).
- Этот план — серверная часть. Парные планы: `android-kolco24/docs/plans/20261020-offline-member-bracelets.md`,
  `ios-kolco24/docs/plans/20261020-offline-member-bracelets.md`.

## Context (from discovery)

- `src/website/models/tag.py` — `Tag(number, nfc_uid unique, last_seen_at, updated_at, code)`, пул глобальный.
- `src/website/models/race.py` — `Race`; последняя миграция `website`: `0100_team_dnf_reason`.
- `src/apps/mobile/views.py:357-460` — `MemberTagBindView` (`bind/`), `_bind_seen_at` (`:362`), `_new_member_code`,
  ленивый код под `select_for_update`; стек прав `SignedAppPermission + IsMobileUser + CanEditRaceLegend`,
  `ClientIPScopedRateThrottle` / `mobile-write`.
- `src/apps/mobile/views.py:~640-670` — загрузка отметок, создание `MarkPresent(nfc_uid, code, number, ...)`.
- `src/apps/mobile/models.py:247` — `MarkPresent` (поле `code` уже есть, `CharField(64)`; `nfc_uid` хранится сырым);
  последняя миграция `mobile`: `0013_mark_force_counted`. `Mark`/`MarkPresent` в админке **не** зарегистрированы.
- `src/apps/mobile/versioning.py:188` — `active_member_tags()` / `member_tags_version(rows=...)`.
- `src/apps/mobile/views.py:1084` — `MemberTagsView` (передаёт готовые `rows` в `member_tags_version`, `:1110`),
  `:1122` — `SyncView` с `versions.member_tags`.
- Прочие читатели `Tag`: `src/api/views/tag.py` (`MemberTagListCreateView`, touch по `nfc_uid` `:127`),
  `src/api/serializers/tag.py:15` (`UniqueValidator`), `src/apps/race/app_data.py:128-133` (`_tag_pool()` —
  `{uid: number}` по всем `Tag`, страница app-data).
- Два сервера (`src/apps/mobile/README.md`, «Два сервера и хэндофф»): до гонки правда в облаке, LAN получает копию
  БД (`backup_db`/`restore_db`), во время гонки правда на LAN. `MOBILE_DATA_SOURCE` = `cloud`/`local`.
- `src/apps/mobile/crypto.py` — примитивы `cryptography`; БД в модели угроз доверенная.
- Троттлинг: определены только scope `mobile-login`, `mobile-write`, `mobile-photo`; GET-эндпоинты без троттлинга.
- `src/apps/mobile/tests.py` — хелперы `_signed_post`, `_signed_post_auth`, `_make_active_token`,
  `_make_admin_race`; образец — тесты `test_member_tag_bind_*`.

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
- maintain backward compatibility: старое тело `bind/` без `code`, браслеты v1, старые клиенты `member_tags`
- работа в ветке (не `master`); `make format && make lint` перед каждым коммитом

## Testing Strategy

- **unit tests**: pytest с `@pytest.mark.django_db` в `src/apps/mobile/tests.py` (и `src/apps/race/tests.py` для
  app-data), подписанные запросы через существующие хелперы.
- **общий тестовый вектор** (одинаковый в трёх репо): ключ `000102…1f` (32 байта), UID `04A1B2C3D4E5F6`,
  `race_id` 42, номер 123 → `code` = `a6a6795129a8cdd661a9bb15`, запись = `4b323422002a007ba6a6795129a8cdd661a9bb15`.
- **e2e tests**: в проекте нет.
- Команды: `uv run pytest src/apps/mobile/tests.py --reuse-db -k <name>`, полный — `make test`.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

- **Ключ гонки** — случайные 32 байта в `Race.member_key`. **Единственный источник — облако**: ключ создаётся
  там (лениво на `member_key/` или командой `ensure_member_keys`) и попадает на LAN вместе с копией БД. LAN-сервер
  (`MOBILE_DATA_SOURCE=local`) ключ **никогда не создаёт**: нет ключа → `404`. Так у двух серверов не может быть
  разных ключей.
- **`code` детерминирован**: одни входы → один код. Перезапись и повторная отправка идемпотентны; `Tag.code` для
  v2 не нужен — сервер всегда пересчитает.
- **`Tag` привязывается к гонке**: `race` FK `null=True` (`null` — старые глобальные браслеты v1), уникальность
  `(race, nfc_uid)`. Один физический браслет можно перезаписать на следующую гонку.
- **`bind/` становится загрузкой привязок**: судья пишет браслет офлайн и отправляет `{nfc_uid, number, code}`
  из очереди на тот сервер, куда сейчас идут админские запросы. Сервер проверяет HMAC и создаёт/обновляет `Tag`.
  Сверка LAN → облако после гонки — существующая backend-задача, не эта.
- **`MarkPresent.code_ok`** — результат проверки при загрузке отметки. Сервер ничего не отклоняет; флаг видно на
  странице app-data команды, решение о снятии за судьёй.
- **Минимальной версии приложения нет** и не добавляем: старое приложение участника увидит браслет v2 через пул,
  когда привязка дойдёт до сервера.

## Technical Details

Запись на чипе v2 (20 байт, страницы 4–8 — тот же размер и FAST_READ `3A 04 08`, что у v1):

```
стр. 4    'K' '2' '4' 0x22          ← version=2 (старший полубайт), type=0x2 (участник)
стр. 5    race_id u16 BE, number u16 BE
стр. 6–8  code = HMAC-SHA256(K_race, uid_bytes ‖ race_id u16 BE ‖ number u16 BE)[:12]
```

- `uid_bytes` — сырые байты UID: `bytes.fromhex(nfc_uid.strip().upper())`, обычно 7 байт.
- `race_id` и `number` — `1 … 65535`. Сервер отказывает (`400`) на `number > 65535`, ключ гонки
  с `race_id > 65535` не выдаётся.
- `code` на проводе — 24 hex (v2) или 32 hex (v1, старый случайный код), регистр любой.

Одна функция `member_code(key, nfc_uid, race_id, number) -> bytes` (`src/apps/mobile/crypto.py`), бросает `ValueError`
на плохой UID/диапазон. Сравнение — `hmac.compare_digest` над **байтами** (`bytes.fromhex(code)`).

`GET /app/race/<id>/member_key/` → `200 {"race_id": 42, "key": "<64 hex>"}`, заголовок `Cache-Control: no-store`.
Права как у `bind/`, throttle `mobile-write`. Неопубликованная гонка или `race_id > 65535` → `404`. На
`MOBILE_DATA_SOURCE=local` без ключа → `404 {"detail": "Ключ гонки не загружен"}`.

`POST bind/` с `code`:

| Статус | Когда |
|---|---|
| `201` | `(race, uid)` неизвестен → создан `Tag(race, nfc_uid, number)` |
| `200` | `(race, uid)` уже есть — номер обновлён на присланный (перезапись браслета судьёй) или совпал |
| `400` | нет ключа у гонки, `code` не совпал с HMAC, плохая длина/hex, `number > 65535` |

`409` для v2 нет: верный HMAC от админа гонки — достаточное основание перепривязать. Тело без `code` — старое
поведение (v1, только глобальные `Tag`, случайный код, `409` на другой номер).

`code_ok` для `MarkPresent` (`check_member_code(race, nfc_uid, number, code)`):
- `nfc_uid` нормализуется (`strip().upper()`) перед `fromhex` и поиском `Tag`;
- `code` 24 hex → `compare_digest(member_code(race.member_key, uid, race.id, number), bytes.fromhex(code))`;
  нет ключа у гонки, не-hex, `ValueError` → `False`;
- `code` 32 hex → сравнение с `Tag.code` глобальной строки (`race IS NULL`) по `nfc_uid`; нет `Tag`/кода → `None`;
- пустой `code`, sentinel `nfc_uid=None` → `None`.

Пул `member_tags` гонки: все строки гонки + глобальные строки в прежнем 30-дневном окне, **кроме** глобальных
с тем же `nfc_uid`, что у строки гонки (строка гонки побеждает). Окно `MAX(last_seen_at)` считается только по
глобальным строкам — и в `active_member_tags`, и в `_bind_seen_at`.

## What Goes Where

- **Implementation Steps**: модели и миграции, `crypto.member_code`, эндпоинт и команда ключа, `bind/` с `code`,
  `code_ok`, пул по гонке, app-data, тесты, README/CLAUDE.md.
- **Post-Completion**: выкатка раньше приложений, `ensure_member_keys` перед бэкапом на LAN, проверка на реальном браслете.

## Implementation Steps

### Task 1: Функция `member_code` и общий тестовый вектор

**Files:**
- Modify: `src/apps/mobile/crypto.py`
- Modify: `src/apps/mobile/tests.py`

- [x] добавить `member_code(key: bytes, nfc_uid: str, race_id: int, number: int) -> bytes` (12 байт) в `crypto.py`
- [x] `ValueError` на `race_id`/`number` вне `1..65535` и на не-hex `nfc_uid`; UID нормализуется внутри
- [x] тест вектора: ожидаемый `code` `a6a6795129a8cdd661a9bb15`
- [x] тесты: детерминизм, разные `uid`/`race_id`/`number` → разные коды, UID в нижнем регистре и с пробелами, ошибки диапазона
- [x] run tests - must pass before task 2

### Task 2: `Race.member_key`, эндпоинт ключа и команда `ensure_member_keys`

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0101_race_member_key.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`
- Create: `src/apps/mobile/management/commands/ensure_member_keys.py`

- [x] `member_key = models.BinaryField(null=True, blank=True)` в `Race` (не редактируется в админке)
- [x] `ensure_member_key(race_id)` (`apps/mobile/member_key.py`): условный `Race.objects.filter(pk=…, member_key__isnull=True).update(member_key=os.urandom(32))`
      и перечитать — без `select_for_update`, без касания `updated_at` (ключ не должен двигать ETag `races`, это сознательно)
- [x] `MemberKeyView` (`GET race/<id>/member_key/`, name `member_key`): права как у `MemberTagBindView`, throttle
      `mobile-write`, `Cache-Control: no-store`; на `MOBILE_DATA_SOURCE=local` ключ не создаётся, нет ключа → `404`
- [x] `manage.py ensure_member_keys [--race <id>]` — создать недостающие ключи опубликованных гонок (запускать в облаке перед `backup_db`)
- [x] тесты: `200` и стабильный ключ, повторное создание не меняет ключ, `Race.updated_at` не меняется,
      не-админ → `403`, без токена → `401`, неопубликованная → `404`, local-режим без ключа → `404` и ключ не создан,
      local-режим с ключом → `200`
- [x] тесты команды; тест, что ключ не попадает ни в один другой GET (`races`, `sync`, legend, teams, member_tags)
- [x] run tests - must pass before task 3

### Task 3: `Tag.race` и пул по гонке

**Files:**
- Modify: `src/website/models/tag.py`
- Create: `src/website/migrations/0102_tag_race.py`
- Modify: `src/website/admin.py`
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/api/views/tag.py`
- Modify: `src/api/serializers/tag.py`
- Modify: `src/apps/race/app_data.py`

- [x] `race = FK(Race, null=True, blank=True, on_delete=CASCADE, related_name="member_tags")`; `race` в `TagAdmin`
- [x] заменить `unique=True` у `nfc_uid` на `UniqueConstraint(race, nfc_uid)` и
      `UniqueConstraint(nfc_uid, condition=Q(race__isnull=True))` (глобальные v1 остаются уникальными)
- [x] `active_member_tags(race_id=None)` и `member_tags_version(rows=None, race_id=None)` по правилу из Technical Details
      (строка гонки вытесняет глобальную с тем же UID; окно по глобальным); `_bind_seen_at` — тоже по глобальным;
      пробросить `race_id` в `MemberTagsView` и `SyncView` (без `race_id` — только глобальный пул, как раньше)
- [x] старый `bind/` (без `code`), `api` `MemberTagListCreateView` (GET и POST) и touch ищут только `race IS NULL`;
      `UniqueValidator(queryset=Tag.objects.filter(race__isnull=True))`
- [x] `app_data._tag_pool(race)`: строки гонки + глобальные, строка гонки побеждает
- [x] тесты пула: гонка A не видит строки гонки B, видит глобальные; один UID глобально и в гонке → одна строка с номером гонки;
      строки гонки без окна, окно — по глобальным; ETag меняется от строки своей гонки
- [x] тесты: тот же UID в двух гонках и глобально — допустимо; дубль в одной гонке — `IntegrityError`;
      `POST /api/member_tag/` с дублем глобального UID → `400`; api игнорирует строки гонки; старый `bind/` игнорирует строки гонки
- [x] тест `_tag_pool`: один UID в двух гонках → номер своей гонки
- [x] прогнать существующие `member_tag*`, judge_scans и app-data тесты, обновить, если изменилось поведение
- [x] run tests - must pass before task 4

### Task 4: `bind/` принимает `code` (загрузка привязок v2)

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/views.py`

- [x] `MemberTagBindSerializer`: необязательный `code` (24 hex); при наличии `code` — `number` обязателен, `1..65535`, UID — hex
- [x] ветка v2 в `MemberTagBindView.post`: нет `member_key` у гонки или HMAC не совпал → `400`
- [x] нет строки → создать `Tag(race, nfc_uid, number)` → `201`; есть → обновить `number` (с `updated_at`) → `200`;
      параллельная вставка — через `get_or_create` (сам перечитывает на `IntegrityError`)
- [x] ответ v2: `{number, nfc_uid}` без `code`
- [x] тесты: `201`, повтор → `200`, другой номер с верным HMAC → `200` и номер обновлён (ETag двигается), плохой HMAC → `400`,
      код чужой гонки → `400`, гонка без ключа → `400`, `number > 65535` → `400`, не-hex UID → `400`, глобальный пул не тронут,
      без `code` — старое поведение не изменилось
- [x] run tests - must pass before task 5

### Task 5: `MarkPresent.code_ok` при загрузке отметок

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0014_markpresent_code_ok.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/race/app_data.py`
- Modify: шаблон app-data команды (рядом с `_present_label`)

- [x] `code_ok = models.BooleanField(null=True)` в `MarkPresent`
- [x] `check_member_code(race_id, key, nfc_uid, number, code, v1_codes) -> bool | None` (`apps/mobile/member_key.py`)
      по правилам из Technical Details; v1-коды одним запросом `Tag` на всю пачку (`v1_codes_for`), без N+1
- [x] выставлять `code_ok` при создании `MarkPresent` в загрузке отметок
- [x] страница app-data команды: у участника отметки значок «⚠» и класс `forged` при `code_ok = False`
      (`None` — без значка)
- [x] тесты: v2 верный → `True`, hex в верхнем регистре → `True`, сырой UID с пробелами/нижним регистром → `True`,
      v2 с чужим номером/UID → `False`, не-hex → `False`, гонка без ключа → `False`, v1 верный/неверный,
      v1 без `Tag` → `None`, sentinel → `None`, пустой `code` → `None`
- [x] тест: число запросов при пачке отметок не растёт с числом участников
- [x] тест app-data: значок `code_ok = False` выводится
- [x] run tests - must pass before task 6

### Task 6: Verify acceptance criteria

- [ ] пройти Overview и Technical Details: формат, ключ (облако — единственный источник), `bind/`, `code_ok`, пул по гонке
- [ ] проверить обратную совместимость: старое тело `bind/`, v1-коды в отметках, старый GET `member_tags`, `api/member_tag`
- [ ] `make format && make lint`
- [ ] полный прогон `make test`

### Task 7: [Final] Update documentation

- [ ] `src/apps/mobile/README.md`: формат v2, `member_key/` (облако — источник, LAN не создаёт), `ensure_member_keys`,
      новая ветка `bind/`, `code_ok`, пул по гонке; снять «принятый риск» глобального пула для v2
- [ ] CLAUDE.md, блок `apps.mobile`: короткий пункт про v2, `member_key/` и `code_ok`; обновить пункт **Member tags** (пул по гонке)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Порядок выкатки (общий для трёх репо):**
1. Сервер (этот план) — обратно совместим, выкатывается первым.
2. Приложения участника читают v2 и шлют `code` в отметках.
3. Судейская запись v2.

**Перед гонкой с LAN-сервером:** в облаке `manage.py ensure_member_keys --race <id>`, затем `backup_db` → `restore_db` на LAN.
Судьи скачивают ключ, пока есть интернет.

**Manual verification:**
- записать браслет v2 судейским телефоном без сети, отправить очередь, сверить `Tag` и `code_ok` у отметки
- подменить номер на чипе вручную → отметка с `code_ok = False`

**Операционное:**
- перевыпуск ключа гонки — только из shell (`Race.member_key = None`, затем `ensure_member_keys`): `BinaryField` в
  админке не редактируется; все v2-браслеты гонки станут `code_ok = False`
