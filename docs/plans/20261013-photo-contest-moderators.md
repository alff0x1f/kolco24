# Администраторы фотоконкурса

## Overview
- Суперюзер назначает пользователей администраторами конкретного фотоконкурса.
- Администратор модерирует присланные фото на своей странице на сайте (без `is_staff` и `/admin/`):
  принимает, отклоняет, возвращает на модерацию.
- Ещё он видит неопубликованный конкурс, открывает оригиналы фото своего конкурса и видит email автора.
- Сейчас модерация есть только в `/admin/` (`PhotoEntryAdmin`, массовые действия), куда нужен `is_staff`.

## Context (from discovery)
- `src/apps/photo_contest/` — `models.py` (`PhotoContest` → `PhotoNomination` → `PhotoEntry`), `views.py`
  (`_visible_contest`, `_contest_context`, `PhotoOriginalView` с проверкой `view_photoentry`), `urls.py`, `admin.py`
  (класс `PhotoContestAdmin` занимает это имя), `tests.py`.
- `src/templates/photo_contest/contest.html` — общий цикл `{% for message in messages %}` и баннер
  «Конкурс не опубликован — его видят только сотрудники».
- Образец: `src/apps/race/views.py:RaceAdminsView` (~221) — add/remove по email/ID, PRG на якорь, сообщения с
  `extra_tags`; `_find_user` (~256); `_row_id` (~499).
- `src/website/models/race.py:RaceAdmin` — образец модели; `apps/race/permissions.py:can_edit_race` — суперюзер
  без строки не получает прав.
- `apps.photo_contest` не зависит от `website`/`apps.race` — так и оставляем (общий хелпер идёт в `apps.accounts`).

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest --reuse-db src/apps/photo_contest src/apps/accounts src/apps/race`
- maintain backward compatibility (race admins behave exactly as before)

## Testing Strategy
- **unit tests**: pytest functions with `@pytest.mark.django_db`, `client`/`django_user_model` fixtures, in
  `src/apps/photo_contest/tests.py` and `src/apps/accounts/tests.py`.
- **e2e tests**: project has none.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- Отдельная модель `PhotoContestModerator` (contest, user), без ролей. Одна роль = модерация.
- `can_moderate(user, contest)` — только по строке модели; суперюзер без строки — `False` (как у гонок).
  Суперюзер может назначить себя.
- Страница модерации `/photo/<slug>/moderate/` + POST смены статуса одного фото.
- Блок «Администраторы конкурса» на странице конкурса: суперюзер добавляет/убирает, модератор видит список.
- `find_user` выносится из `RaceAdminsView` в `apps/accounts/users.py` и используется обоими view.
- Вне объёма: массовые действия, журнал изменений, причина отказа, уведомления, редактирование конкурса
  администратором, регистрация модели в `/admin/`.

## Technical Details

**Модель** (`models.py`):
```python
class PhotoContestModerator(models.Model):
    contest = models.ForeignKey(PhotoContest, on_delete=models.CASCADE, related_name="moderators")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="moderated_photo_contests")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("contest", "user")
        ordering = ["created_at", "pk"]
        verbose_name = "администратор конкурса"
        verbose_name_plural = "администраторы конкурса"
```

**Права** (`permissions.py`):
```python
def can_moderate(user, contest):
    return user.is_authenticated and contest.moderators.filter(user=user).exists()
```
- `_visible_contest`: неопубликованный конкурс виден `is_staff or is_superuser or can_moderate` (`is_superuser` —
  чтобы PRG после назначения не 404-ил суперюзеру без `is_staff`). Возвращает `(contest, can_moderate)`, чтобы
  `_contest_context` не считал флаг второй раз.
- `PhotoOriginalView`: `has_perm("photo_contest.view_photoentry")` → пускаем; иначе entry по pk (404) и
  `PhotoContestModerator.objects.filter(contest_id=entry.nomination.contest_id, user=user).exists()`, иначе 404.
  Аноним, обычный пользователь, модератор чужого конкурса и неизвестный `entry_id` получают одинаковый 404.

**URL** (`urls.py`, до `<slug:slug>/` конфликтов нет — пути длиннее):
| name | path | method |
|---|---|---|
| `photo_moderate` | `<slug:slug>/moderate/` | GET |
| `photo_moderate_entry` | `<slug:slug>/moderate/<int:entry_id>/` | POST |
| `photo_contest_moderators` | `<slug:slug>/moderators/` | POST |

**Гейт страницы модерации** (общий хелпер в `views.py`, возвращает `(contest, response)`): конкурс по slug без
фильтра `is_published` (404); аноним → `login?next=<url страницы модерации>`; не модератор → 403. Для
неопубликованного конкурса 403 выдаёт, что slug существует — принято, так же работает `_load_race_for_admin`.
Право `view_photoentry` без строки модератора не даёт доступа к странице (только к оригиналам).

**GET `photo_moderate`**:
- `?status=` ∈ `pending` (по умолчанию) / `approved` / `rejected` / `all`; другое → `pending`.
- `?nomination=<id>` — только номинации этого конкурса; чужой/нечисловой id → все.
- Счётчики вкладок: один `values("status").annotate(Count("pk")).order_by()` с учётом фильтра номинации;
  «Все» = сумма.
- Queryset: `PhotoEntry.objects.filter(nomination__contest=contest)`, `select_related("nomination", "uploaded_by")`,
  `order_by("created_at", "pk")`; `Paginator(qs, 48)`, `get_page(request.GET.get("page"))` — именно `get_page`:
  после смены статуса последняя страница может исчезнуть, `get_page` отдаст последнюю существующую, `page()` бы 404-ил.
- Ссылки вкладок/номинаций/страниц сохраняют остальные параметры. В Django 4.2 нет `{% querystring %}` (он с 5.1),
  поэтому URL собираются во view через `urlencode` и передаются готовыми (`tab_urls`, `nomination_urls`,
  `page_url_prefix`).
- Сообщений после смены статуса нет (обратная связь — карточка уходит из вкладки). Если их добавят, их надо
  выводить в `moderate.html`, иначе они всплывут на странице конкурса.
- Карточка: превью, подпись, номинация, `uploaded_by.email`, дата, `width×height/1e6` Мп и `size_bytes` в МБ,
  ссылка «Оригинал» (`photo_original`, `target="_blank" rel="noopener"`), статус, формы-кнопки на два других статуса.
  Каждая форма: `csrf_token`, `status`, скрытое `next` = `request.get_full_path`.

**POST `photo_moderate_entry`**: тот же гейт → entry по `pk` и `nomination__contest=contest` (404) → `status not in
dict(PhotoEntry.STATUS_CHOICES)` → 400 (не `in STATUS_CHOICES` — это список кортежей) →
`entry.status = status; entry.save(update_fields=["status"])` → redirect на `next`, если
`url_has_allowed_host_and_scheme(next, allowed_hosts={request.get_host()}, require_https=request.is_secure())`,
иначе на `photo_moderate`.

Лимит фото не проверяется: возврат отклонённого фото может дать автору «4 из 3» в номинации (если он уже загрузил
замену). Это решение модератора — так же работает «Принять» в `/admin/`.

**POST `photo_contest_moderators`** (`PhotoContestModeratorsView`, копия логики `RaceAdminsView` без ролей):
- конкурс по slug (404); аноним → `login?next=<url конкурса>`; не суперюзер → 403; `action` не `add`/`remove` → 400.
- `add`: `user` пусто → «Укажите email или ID пользователя.»; длиннее 254 → «Слишком длинное значение.»;
  `find_user` → `None` → «Пользователь «…» не найден.»; `get_or_create` → «… назначен(а) администратором.» или
  «… уже администратор.».
- `remove`: `filter(contest=contest, user_id=<int или None>).delete()` → «Администратор снят.» / «Не найден.».
  Приведение к int — локальная функция в `photo_contest/views.py` (не импорт `_row_id` из `apps.race`: приложение не
  зависит от гонок). Число вне диапазона bigint → `None`, чтобы не было 500.
- сообщения с `extra_tags="contest-moderators"`; redirect на `photo_contest` + `#contest-moderators`.
- Неопубликованный конкурс тоже доступен (суперюзер готовит его до запуска).

**Страница конкурса** (`_contest_context` + `contest.html`):
- контекст: `can_moderate`, `pending_count` (только для модератора), `moderators` (для суперюзера и модератора,
  `select_related("user")`), `can_manage_moderators = user.is_superuser`.
- блок `#contest-moderators` выводится по флагам `can_manage_moderators or can_moderate`, **не** по непустому
  `moderators`: иначе после удаления последнего модератора пропадут форма и сообщение «Администратор снят.».
  Пустой список — текст «Пока никого нет».
- строка списка: имя + ID (как `race_page.html`); email — только суперюзеру, модератор чужие email не видит.
- общий цикл сообщений пропускает сообщения с тегом `contest-moderators`
  (`{% if "contest-moderators" not in message.tags %}`); блок выводит только их. Двойной обход `messages` в одном
  шаблоне безопасен (хранилище держит сообщения весь запрос), `base-2.html` сообщений не выводит.
- блок «Модерация · N на проверке» со ссылкой на `photo_moderate` — модератору.
- баннер неопубликованного конкурса: «его видят только сотрудники и администраторы конкурса».

**`find_user`** (`src/apps/accounts/users.py`): перенос `RaceAdminsView._find_user` без изменений.

## What Goes Where
- **Implementation Steps**: код, шаблоны, CSS, тесты, CLAUDE.md.
- **Post-Completion**: ручная проверка в браузере, миграция на проде.

## Implementation Steps

### Task 1: Вынести find_user в apps.accounts

**Files:**
- Create: `src/apps/accounts/users.py`
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/accounts/tests.py`

- [ ] создать `find_user(query)` в `src/apps/accounts/users.py` — тело `RaceAdminsView._find_user` без изменений
- [ ] в `RaceAdminsView._add` звать `find_user`, удалить `_find_user` и ставший лишним импорт `get_user_model` (если не используется)
- [ ] написать тесты `find_user`: ID цифрами, email в другом регистре, `"²"` → поиск по email (None), 19 цифр → None, неизвестный email → None
- [ ] запустить тесты гонок (`uv run pytest --reuse-db src/apps/race`) — должны пройти без изменений

### Task 2: Модель PhotoContestModerator и can_moderate

**Files:**
- Modify: `src/apps/photo_contest/models.py`
- Create: `src/apps/photo_contest/migrations/0002_photocontestmoderator.py`
- Create: `src/apps/photo_contest/permissions.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] добавить модель `PhotoContestModerator` (см. Technical Details)
- [ ] `uv run python src/manage.py makemigrations photo_contest`
- [ ] создать `permissions.py` с `can_moderate`
- [ ] написать тесты `can_moderate`: модератор — True; аноним, обычный пользователь, суперюзер без строки, модератор другого конкурса — False
- [ ] тест уникальности пары (contest, user) — `IntegrityError`
- [ ] запустить тесты — должны пройти

### Task 3: Права модератора на существующих страницах

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/templates/photo_contest/contest.html`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `_visible_contest`: неопубликованный конкурс виден `is_staff or is_superuser or can_moderate`; возвращает `(contest, can_moderate)`, обновить вызовы
- [ ] `PhotoOriginalView`: пускать по праву `view_photoentry` или по строке модератора конкурса фото, иначе 404
- [ ] поправить текст баннера неопубликованного конкурса
- [ ] тесты: неопубликованный конкурс — модератор 200, суперюзер без `is_staff` 200, обычный пользователь 404
- [ ] тесты оригинала: модератор своего конкурса 200, модератор другого конкурса 404, аноним 404, суперюзер 200, модератор + неизвестный `entry_id` 404
- [ ] запустить тесты — должны пройти

### Task 4: Страница модерации (GET)

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Create: `src/templates/photo_contest/moderate.html`
- Create: `src/static/css/photo_moderate.css`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] хелпер гейта модерации (404 / login / 403), `PhotoModerateView.get`, URL `photo_moderate`
- [ ] фильтры `status`/`nomination`, счётчики вкладок, сортировка, `Paginator(48)` + `get_page`
- [ ] готовые URL вкладок/номинаций/страниц во view (`urlencode`, без `{% querystring %}`)
- [ ] шаблон на `website/base-2.html`: вкладки, фильтр номинаций, сетка карточек, формы-кнопки, пагинация; обёртка `.photo-moderate` (без голого `.page`), ручная разметка
- [ ] CSS в `photo_moderate.css`, подключить через `{% block extra_head %}`; без внешних ресурсов
- [ ] тесты: аноним → login с `next`, обычный пользователь, суперюзер без строки и staff с `view_photoentry` без строки → 403, модератор → 200 (и для неопубликованного конкурса), несуществующий slug → 404
- [ ] тесты: по умолчанию только `pending`; `?status=approved|rejected|all`; неизвестный статус → pending; `?nomination=` своей и чужой номинации; счётчики учитывают фильтр номинации и не считают фото другого конкурса; email автора и ссылка на оригинал в ответе; фото другого конкурса не попадают
- [ ] тесты: ссылка вкладки сохраняет `nomination`, ссылка страницы — `status` и `nomination`; `?page=999` и `?page=abc` → 200
- [ ] запустить тесты — должны пройти

### Task 5: Смена статуса фото (POST)

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `PhotoModerateEntryView.post`, URL `photo_moderate_entry`
- [ ] поиск entry в рамках конкурса, проверка статуса через `dict(PhotoEntry.STATUS_CHOICES)` (400), `save(update_fields=["status"])`
- [ ] redirect на безопасный `next`, иначе `photo_moderate`
- [ ] тесты: принять / отклонить / вернуть на модерацию; redirect на `next` с параметрами; без `next` → `photo_moderate`
- [ ] тест: возврат отклонённого фото при полной номинации разрешён (у автора становится больше лимита)
- [ ] тесты ошибок: неизвестный статус → 400 и статус не изменился; фото другого конкурса → 404 и не изменилось; аноним → login; не модератор → 403; GET → 405; внешний `next` (`https://evil.example/`, `//evil.example/`, `https:///evil`) → `photo_moderate`
- [ ] запустить тесты — должны пройти

### Task 6: Назначение администраторов на странице конкурса

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/templates/photo_contest/contest.html`
- Modify: `src/static/css/photo_contest.css`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `PhotoContestModeratorsView.post` (add/remove, см. Technical Details), URL `photo_contest_moderators`
- [ ] `_contest_context`: `can_moderate`, `pending_count`, `moderators`, `can_manage_moderators`
- [ ] в `contest.html`: блок «Модерация · N на проверке» для модератора; блок `#contest-moderators` по флагам прав, не по непустому списку (имя + ID; email, форма и «Убрать» только суперюзеру; пустое состояние); общий цикл сообщений пропускает тег `contest-moderators`, блок выводит только его
- [ ] тесты add: по email в другом регистре, по ID, повторно (одна строка), неизвестный, пустой, длиннее 254; суперюзер добавляет себя
- [ ] тесты remove: удаляет; `user_id` модератора другого конкурса — ничего не удалено; мусорный и 25-значный `user_id` — без 500
- [ ] тест: удаление единственного модератора → `follow=True` → блок с формой на месте, «Администратор снят.» внутри блока
- [ ] тесты гейта: аноним → login, обычный пользователь и модератор → 403, плохой `action` → 400, redirect на `#contest-moderators`
- [ ] тесты страницы конкурса: суперюзер видит форму и email; модератор — список без формы и без чужих email, плюс блок «N на проверке»; обычный пользователь — ни того ни другого; сообщение `contest-moderators` выводится только в блоке
- [ ] запустить тесты — должны пройти

### Task 7: Verify acceptance criteria
- [ ] проверить все требования из Overview
- [ ] `make format && make lint`
- [ ] полный прогон: `uv run pytest`
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` — нет лишних миграций

### Task 8: [Final] Update documentation
- [ ] CLAUDE.md, абзац `apps.photo_contest`: 2–3 предложения — модель `PhotoContestModerator`, `can_moderate` без суперюзера (как у гонок), страница `photo_moderate`, назначение только суперюзером на странице конкурса, возврат отклонённого фото не проверяет лимит, `find_user` в `apps/accounts/users.py`
- [ ] перенести план в `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- суперюзер назначает себя и другого пользователя на странице конкурса, снимает одного
- модератор открывает неопубликованный конкурс, страницу модерации, оригинал фото; меняет статусы, фильтры и
  страница сохраняются после смены
- вёрстка страницы модерации на ширине телефона

**Deployment:**
- миграция `photo_contest/0002` применяется при обычном деплое
