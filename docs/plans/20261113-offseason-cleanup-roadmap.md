# Дорожная карта: чистка и безопасность в межсезонье

## Overview
- Гонка раз в год, все участники на последней версии приложения. Android (`../android-kolco24`) и iOS (`../ios-kolco24`) ходят **только** в `/app/*`. Старый `/api/*` никто не зовёт, но он открыт без авторизации: любой читает составы команд и создаёт метки.
- Цель: закрыть утечку, убрать мёртвый код, обновить Django (поддержка 4.2 кончилась в апреле 2026), убрать старый Bootstrap-стек.
- Пять шагов в порядке риска. **Каждый шаг — отдельный небольшой PR**, выкатывается и откатывается сам по себе.
- Новых функций нет. Исключение — Task 11: окно приёма и лимит кадров для загрузок `/app/` (защита диска).

## Context (from discovery)
- Старый план `docs/plans/20260919-remove-legacy-api.md` — черновик-инвентаризация. Его главный блокер снят: привязка браслетов есть в `POST /app/race/<id>/member_tags/bind/`, Android её использует.
- Протокол читает `apps.mobile.Mark`, а не старый `TakenKP` (`src/apps/race/results.py`).
- `src/api/`: views `tag.py`, `teams.py`, `checkpoint.py`, `contributors.py`; сериализаторы с теми же именами; `tests.py` (484 строки). Снаружи `src/api` их никто не импортирует.
- `src/website/urls.py:287` — `api/race/<id>/upload_photo/`, открытая загрузка файла, пишет `TakenKP`.
- `src/config/settings.py:30` — `SECRET_KEY` с запасным значением `"changeme"`; настроек `SECURE_*` и secure-cookie нет; `USE_L10N` (строка 240) удалён в Django 5.0; `STORAGES` задан только при `DEBUG=False`.
- `deploy/kolco24.env` с боевыми секретами не в `.gitignore`.
- `deploy/nginx.conf` слушает 80 и шлёт `X-Forwarded-Proto $scheme` — это всегда `http`. TLS кончается на внешнем прокси.
- `pyproject.toml`: `crypto~=1.4.1` нигде не импортируется.
- На `base.html` остались: `accounts/password_reset*.html` (4 шт.), `accounts/impersonate.html`, `website/regulations.html`, `website/edit_page.html`, `donate/index.html`, плюс мёртвые `team_points.html`, `teams_result.html`, `website/team_member_race_logs.html`, `website/index_dummy.html`.
- CI гоняет Postgres 16, в проде 15.

## Принятые решения
- `/api/*` удаляем сразу, без периода с 410.
- Остаются `GET /api/ping/` и `GET /api/contributors/`.
- **Только код.** Модели, таблицы, колонки и регистрации в `/admin/` не трогаем. Разрушающих миграций нет.
- `athlet1..6` не мёртвые (их читает лист выдачи) — не трогаем.
- `/regulations/` оставляем: ссылка может жить в строке `MenuItem` или снаружи.
- `is_photo_upload_enabled` для новой загрузки фото **не** используем: ручной флаг легко забыть включить или выключить. Вместо него — окно по датам гонки и лимит кадров (Task 11).
- Окно приёма `/app/` загрузок: от `race.date - 1 день` до `race.date_end + 7 дней` включительно (локальная дата). Вне окна — `403`: клиент останавливает загрузку цели и пробует снова при следующей синхронизации, данные на телефоне не теряются.
- Лимит кадров на одну отметку: 20. Сверх лимита — `413`: клиент считает это ошибкой одного кадра (`isHardFrameFailure`) и продолжает с другими отметками. `403` здесь не подходит — он останавливает всю загрузку фото.

## Development Approach
- **testing approach**: Regular (сначала код, потом тесты).
- Тесты в стиле pytest (`@pytest.mark.django_db`, фикстуры `client`/`django_user_model`).
- Перед каждым коммитом: `make format`, `make lint`.
- Каждая задача заканчивается тестами; следующая не начинается, пока тесты не зелёные.
- Правки `CLAUDE.md` минимальные: файл уже огромный, только убрать или поправить устаревшие строки.
- При смене объёма работ — обновить этот файл.

## Testing Strategy
- **unit tests**: в каждой задаче. Для удалений — тест «маршрут отдаёт 404» и проверка, что соседние живые маршруты работают.
- **e2e tests**: в проекте их нет. Ручные проверки вынесены в Post-Completion.
- Полный прогон: `uv run pytest`.

## Progress Tracking
- готовое помечать `[x]` сразу
- новые задачи — с префиксом ➕
- блокеры — с префиксом ⚠️

## What Goes Where
- **Implementation Steps** (`[ ]`): то, что делается в этом репозитории.
- **Post-Completion** (без чекбоксов): проверки на проде и тестовом стенде, внешний прокси, приложения.

## Implementation Steps

### Task 1: Секреты и настройки (PR 1)

**Files:**
- Modify: `.gitignore`
- Modify: `src/config/settings.py`
- Modify: `deploy/kolco24.env.example`
- Modify: `src/api/views/contributors.py`
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `src/api/tests.py`
- Create: `src/config/test_settings_security.py`

- [x] добавить `deploy/kolco24.env` в `.gitignore`
- [x] `SECRET_KEY`: убрать запасное `"changeme"`; при `DEBUG=False` и пустой переменной падать на старте с понятной ошибкой (`ImproperlyConfigured`); при `DEBUG=True` оставить локальное значение. Сборка образа (`DJANGO_SECRET_KEY=build ... collectstatic`) должна работать как раньше
- [x] одна переменная окружения (например `HTTPS_ONLY`, по умолчанию выключена) включает `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER`, `SECURE_HSTS_SECONDS`. По умолчанию выключена, потому что LAN-сервер на старте гонки работает по HTTP
- [x] `SECURE_SSL_REDIRECT` не включать: редирект делает внешний прокси
- [x] описать переменную в `deploy/kolco24.env.example`
- [x] `BearerTokenPermission`: сравнение токена через `hmac.compare_digest`
- [x] убрать `crypto~=1.4.1` из `pyproject.toml`, обновить `uv.lock`
- [x] ➕ `requests` приходил только через `crypto`, а его импортирует `src/vtb/client.py` — добавлен прямой зависимостью
- [x] тесты: с выключенной переменной secure-настройки не заданы; с включённой — заданы (логику вынести в маленькую чистую функцию, чтобы тестировать без перезагрузки settings)
- [x] тесты: `contributors/` — верный токен 200, неверный и пустой 403
- [x] `uv run pytest`, `make lint` — зелёные

### Task 2: Удалить открытые эндпоинты `/api/*` (PR 2)

**Files:**
- Modify: `src/api/urls.py`
- Modify: `src/api/views/__init__.py`
- Delete: `src/api/views/tag.py`, `src/api/views/teams.py`, `src/api/views/checkpoint.py`
- Delete: `src/api/serializers/tag.py`, `src/api/serializers/team.py`, `src/api/serializers/checkpoint.py`
- Modify: `src/api/serializers/__init__.py`
- Modify: `src/api/tests.py`
- Modify: `http/api.http`

- [x] убрать маршруты `member_tag/`, `member_tag/touch/`, `race/<id>/checkpoint/`, `race/<id>/checkpoint_tag/`, `race/<id>/teams/`, `race/<id>/teams.csv`
- [x] удалить их views и сериализаторы; оставить `ping/` и `contributors/`
- [x] перед удалением ещё раз проверить grep'ом, что снаружи `src/api` нет импортов (в том числе из `src/apps/mobile/tests.py`)
- [x] удалить тесты удалённых эндпоинтов из `src/api/tests.py`
- [x] почистить `http/api.http`
- [x] ➕ удалён `test_api_member_tag_list_does_not_expose_code` из `src/apps/mobile/tests.py` (ходил в `/api/member_tag/`); поправлены комментарии, ссылавшиеся на удалённые views
- [x] тесты: каждый удалённый маршрут отдаёт 404; `ping/` — 200; `contributors/` с токеном — 200
- [x] `uv run pytest src/apps/mobile/tests.py` — тесты `member_tags` зелёные без touch-эндпоинта
- [x] `uv run pytest`, `make lint` — зелёные

### Task 3: Удалить `upload_photo` и переключатель в форме гонки (PR 2)

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/website/views/views_.py`, `src/website/views/__init__.py`
- Modify: `src/apps/race/forms.py`
- Modify: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`
- Modify: `src/website/tests.py`

- [x] убрать маршрут `api/race/<id>/upload_photo/` и функцию `upload_photo`
- [x] убрать `is_photo_upload_enabled` из `RaceForm.Meta.fields` и переключатель из `race_form.html`; **колонку модели оставить**
- [x] проверить, что сохранение формы гонки не сбрасывает сохранённое значение колонки
- [x] поправить тестовые данные формы гонки (`src/apps/race/tests.py`, ключ `is_photo_upload_enabled`)
- [x] тест: `POST api/race/<id>/upload_photo/` отдаёт 404
- [x] тест: форма гонки сохраняется без поля
- [x] `uv run pytest`, `make lint` — зелёные

### Task 4: Документация после удаления API (PR 2)

**Files:**
- Modify: `src/apps/mobile/README.md`
- Modify: `CLAUDE.md`
- Move: `docs/plans/20260919-remove-legacy-api.md` → `docs/plans/completed/`

- [ ] `src/apps/mobile/README.md`: убрать упоминания удалённых `/api/` эндпоинтов
- [ ] `CLAUDE.md`, блок **Member-tag bind**: строка «Bind never touches `last_seen_at`» уже неверна — bind ставит `last_seen_at = MAX(last_seen_at)` пула (`src/apps/mobile/views.py:_bind_seen_at`), поэтому повторная привязка возвращает устаревший чип v1 в окно. Поправить строку; упоминание «a real scan (api touch) revives it» убрать
- [ ] `CLAUDE.md`: поправить строки про `api` (описание приложения, `CheckpointSerializer`, `CheckpointTagCreateView`, `MemberTagTouchView`, «`api`-app team creation», BOM-less экспорт `api/views/teams.py`). Только правка устаревшего, без новых абзацев
- [ ] в старом плане поставить статус «выполнено» и перенести его в `completed`
- [ ] тестов нет (только документация); `make lint` — зелёный

### Task 5: Удалить мёртвые страницы результатов и логов (PR 3)

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/website/views/views_.py`, `src/website/views/__init__.py`
- Delete: `src/templates/teams_result.html`, `src/templates/team_points.html`
- Delete: `src/templates/website/team_member_race_logs.html`, `src/templates/website/index_dummy.html`
- Modify: `src/website/tests.py`

- [ ] удалить `AllTeamsResultView` и маршрут `…/results-deprecated/` (`category_results_deprecated`)
- [ ] удалить `TeamPointsView` и маршрут `team/<id>/points/` (`team_points`)
- [ ] удалить `TeamMemberRaceLogView`, маршрут `race/<slug>/member_logs/` и int-id редирект `race/<int>/member_logs/`
- [ ] удалить немаршрутизированные `new_point` и `index_dummy`
- [ ] убрать закомментированные строки маршрутов в `src/website/urls.py`
- [ ] остальные int-id редиректы оставить (включая `…/results/` — он ведёт на живой протокол)
- [ ] удалить импорты, ставшие лишними (`TakenKP`, `is_admin` и т.п., если больше нигде не нужны); модели и админку не трогать
- [ ] обновить `src/apps/race/results.py:3` — докстринг ссылается на удалённый view
- [ ] переписать `test_category_results_deprecated_url_renders_old_view` (`src/apps/race/tests.py:5220`): он делает `reverse("category_results_deprecated")` и ждёт 200 — заменить на проверку 404 по буквальному пути
- [ ] grep по тестам на остальные удаляемые имена (`team_points`, `race_member_logs`) и шаблоны
- [ ] тесты: удалённые маршруты отдают 404; `category_results` и int-id редирект на него работают
- [ ] `uv run pytest`, `make lint` — зелёные

### Task 6: Убрать мёртвые константы карт (PR 3)

**Files:**
- Modify: `src/website/forms.py`
- Modify: `CLAUDE.md`

- [ ] удалить `FREE_MAPS` и `MAP_PRICE` из `src/website/forms.py` (копия в `templates/demo/team-register.html` — отдельный макет, не трогать)
- [ ] `CLAUDE.md`: убрать упоминание `FREE_MAPS`/`MAP_PRICE`
- [ ] запасной разбор `ORDER_<int>` в `_resolve_race_payment` **не трогать**: им пользуются и возвраты по старым оплаченным заказам без FK
- [ ] тестов нет (удаление неиспользуемых констант); `uv run pytest`, `make lint` — зелёные

### Task 7: Django 4.2 → 5.2 LTS (PR 4)

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `src/config/settings.py`
- Modify: `.github/workflows/tests.yml`
- Modify: `CLAUDE.md`

- [ ] поднять `Django` до `5.2.x`
- [ ] сверить с 5.2 и при необходимости поднять `djangorestframework`, `django-mailer`, `whitenoise` (6.6.0 старый)
- [ ] удалить `USE_L10N`
- [ ] сделать блок `STORAGES` безусловным; при `DEBUG=True` staticfiles — обычный `StaticFilesStorage`, при `False` — `config.storage.CacheBustingStaticFilesStorage`
- [ ] проверить `config/storage.py` против API 5.2
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` — новых миграций нет
- [ ] один прогон `uv run pytest -W error::DeprecationWarning -W error::PendingDeprecationWarning`; исправить свои предупреждения, сторонние записать сюда
- [ ] CI: Postgres 16 → 15, как в проде
- [ ] `CLAUDE.md`: «Django 4.2 project» → «Django 5.2 project»
- [ ] `uv run pytest` и `make lint` — зелёные; новых тестов не нужно, проверка — весь набор

### Task 8: Перевести страницы аккаунтов и статические на `base-2.html` (PR 5)

**Files:**
- Modify: `src/templates/accounts/password_reset.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`
- Modify: `src/templates/accounts/impersonate.html`
- Modify: `src/templates/website/regulations.html`
- Modify: `src/templates/website/edit_page.html`
- Modify: `src/apps/accounts/tests.py`, `src/website/tests.py`

- [ ] четыре страницы сброса пароля — на `base-2.html`, поля вручную (`class="input"`, ошибки под полем), как на странице входа
- [ ] `impersonate.html` — на `base-2.html`
- [ ] `regulations.html` — на `base-2.html`; маршрут оставить
- [ ] `edit_page.html` — на `base-2.html`; EasyMDE и `markdown-editor.js` подключены как сейчас, `autoDownloadFontAwesome: false` не трогать
- [ ] никаких внешних ресурсов (шрифты, CDN)
- [ ] тесты: каждая страница отдаёт 200 и не содержит `form-control`; сброс пароля проходит целиком (письмо через locmem `EMAIL_BACKEND`); ошибка формы видна под полем
- [ ] `uv run pytest`, `make lint` — зелёные

### Task 9: Перевести страницу пожертвований на `base-2.html` (PR 6)

**Files:**
- Modify: `src/templates/donate/index.html`
- Modify: `src/donate/tests.py`
- Create (если нужно): `src/static/css/donate.css`

- [ ] `donate/index.html` — на `base-2.html`, поля вручную; стили страницы в `donate.css` под своим классом-обёрткой (не `.page`)
- [ ] проверить, какие скрипты старого стека страница использует (jQuery, smartWizard); заменить на vanilla JS или убрать
- [ ] поток оплаты (создание заказа ВТБ, редирект) не менять
- [ ] тесты: страница 200; POST с верными данными создаёт `DonateRequest` и редиректит на оплату (ВТБ замокан); ошибки формы видны
- [ ] `uv run pytest`, `make lint` — зелёные

### Task 10: Удалить Bootstrap-стек (PR 6)

**Files:**
- Delete: `src/templates/website/base.html`
- Delete: `src/static/css/theme.css`, `src/static/css/entypo.css`
- Delete (после grep): `src/static/css/socicon.css`, `src/static/js/bootstrap.min.js`, `jquery-3.2.1.min.js`, `jquery.smartWizard.min.js`, `popper.min.js`, `flickity.pkgd.min.js`, `prism.min.js`, `scrollMonitor.js`, `smooth-scroll.polyfills.min.js`, `zoom.min.js`, `theme.js`, связанные шрифты
- Modify: `CLAUDE.md`

- [ ] grep по `src/templates` и `src/static`: ни один шаблон не наследует `base.html` и не грузит эти файлы (проверить и `templates/demo/`, и `registration/`)
- [ ] удалить `base.html` и только те ассеты, на которые нет ссылок
- [ ] настоящий `collectstatic` с боевым хранилищем в пустой временный каталог: `DEBUG=False DJANGO_SECRET_KEY=build STATIC_ROOT=<tmp> uv run python src/manage.py collectstatic --noinput` — без ошибок. `--dry-run` не годится: он пропускает хеширование и не ловит CSS со ссылкой на удалённый файл. Если `STATIC_ROOT` не читается из окружения — проверить сборкой образа (`docker build`)
- [ ] `CLAUDE.md`, раздел **Template stacks**: оставить один стек
- [ ] тест: обход шаблонов — ни один файл в `src/templates` не содержит `website/base.html`
- [ ] `uv run pytest`, `make lint` — зелёные

### ➕ Task 11: Окно приёма и лимит кадров для загрузок `/app/` (PR 7)

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`
- Modify: `src/apps/mobile/CLAUDE.md`, `src/apps/mobile/README.md`

- [ ] константы `UPLOAD_OPEN_DAYS_BEFORE = 1`, `UPLOAD_CLOSE_DAYS_AFTER = 7`, `PHOTO_MAX_FRAMES_PER_MARK = 20` рядом с `PHOTO_MAX_BYTES`
- [ ] функция `_upload_window_open(race)`: `date - 1 ≤ timezone.localdate() ≤ date_end + 7`
- [ ] проверка окна в `TrackUploadView`, `MarkUploadView`, `MarkPhotoUploadView` сразу после поиска гонки; вне окна — `403 {"detail": "upload window closed"}`. `judge_scans/` не трогать (там токен админа)
- [ ] `MarkPhotoUploadView`: после идемпотентной проверки, если у отметки уже `PHOTO_MAX_FRAMES_PER_MARK` кадров — `413 {"detail": "too many frames"}`. Повтор уже сохранённого кадра по-прежнему `200`
- [ ] гонка за лимит при параллельных запросах допустима (может проскочить 1–2 лишних кадра) — блокировку не добавлять
- [ ] поправить существующие тесты `/track/`, `/marks/`, фото: их гонки должны попадать в окно (дата гонки — сегодня в фикстуре)
- [ ] тесты окна для всех трёх эндпоинтов: день до старта — принято; `date - 2` — `403`; `date_end + 7` — принято; `date_end + 8` — `403` (дата через подмену `timezone.localdate` или даты гонки)
- [ ] тест: при `403` ничего не записано (`TrackPoint`, `Mark`, `MarkPhoto`, файлы)
- [ ] тесты лимита: 20-й кадр — `201`; 21-й — `413`, файла нет; повтор 1-го кадра при полном лимите — `200`
- [ ] `src/apps/mobile/CLAUDE.md`, `README.md`: одной-двумя строками добавить окно и лимит в описание потока (Flow) соответствующих эндпоинтов
- [ ] `uv run pytest`, `make lint` — зелёные

### Task 12: Verify acceptance criteria
- [ ] в `/api/` остались только `ping/` и `contributors/`
- [ ] в `src/website/urls.py` нет мёртвых и закомментированных маршрутов
- [ ] ни одной новой миграции, удаляющей таблицы или колонки
- [ ] Django 5.2, набор зелёный без своих deprecation-предупреждений
- [ ] ни один шаблон не наследует `base.html`
- [ ] полный прогон: `uv run pytest`
- [ ] загрузки `/app/` вне окна дат гонки отдают `403`; 21-й кадр отметки — `413`
- [ ] `make lint`

### Task 13: [Final] Update documentation
- [ ] `README.md` — обновить, если упоминает `/api/` или Django 4.2
- [ ] `CLAUDE.md` — финальная сверка: нет ссылок на удалённые файлы и маршруты
- [ ] перенести этот план в `docs/plans/completed/`

## Post-Completion
*Ручные проверки и внешние системы — без чекбоксов.*

**Перед PR 1 (включение `HTTPS_ONLY` на проде):**
- узнать, какой заголовок шлёт внешний TLS-прокси. Внутренний nginx сейчас перезаписывает `X-Forwarded-Proto` значением `$scheme` (всегда `http`), поэтому `SECURE_PROXY_SSL_HEADER` без правки `deploy/nginx.conf` не заработает и вход может сломаться. Варианты: пробрасывать заголовок внешнего прокси или включить только secure-cookie.
- на LAN-сервере переменную не включать.
- убедиться, что в `deploy/kolco24.env` на проде задан `DJANGO_SECRET_KEY` — иначе после PR 1 сервер не стартует.

**После PR 2:**
- проверить логи nginx за неделю до выкладки: кто ещё ходил в `/api/*` (скрипты, мониторинг). `teams.csv` удаляется; если он нужен организаторам — есть CSV «Команды» (`race_teams_admin_export`).

**После PR 4 (Django 5.2), на тестовом стенде:**
- настоящий платёж ВТБ от начала до конца (заказ, оплата, `check_vtb_payments`, зачисление мест и доп-услуг);
- `runmailer` отправляет письмо входа;
- массовые действия блокировки легенды в `/admin/`;
- подписанный запрос `/app/` из Android и из iOS, включая загрузку фото.
- сделать дамп БД перед выкладкой.

**Перед PR 7:**
- сверить с Android и iOS, сколько кадров клиент реально снимает на одну отметку; лимит 20 должен быть с запасом.
- у прошедших гонок проверить, что `date_end` заполнен верно (по умолчанию он равен дате создания).
- сказать организаторам: после `date_end + 7 дней` треки, отметки и фото не принимаются.

**После PR 5–6:**
- глазами проверить сброс пароля, `/regulations/`, правку страницы и пожертвование на телефоне и на десктопе.

## Later (вне этой дорожной карты)
- Запасной разбор `ORDER_<int>` в `check_vtb_payments`: убрать можно только после миграции данных, которая проставит `Payment.vtb_payment` старым заказам, и теста возврата по старому заказу.
- Удаление старых таблиц и колонок: `TakenKP`, `TeamStartLog`, `TeamFinishLog`, `TeamMemberRaceLog`, `PaymentsYa`, `SbpPaymentRecipient`, `Team.map_count*`, `Payment.map`, `Team.category`, `Checkpoint.year`, `Race.is_photo_upload_enabled`.
- `/marks/`: поддельный `team_id` может выставить чужой команде время старта или финиша; у строк `Mark` нет админ-интерфейса для исправления.
- Возвраты проверяются только ручной командой `check_vtb_payments --order-id`.
- Кэш троттлинга `LocMemCache` — свой у каждого процесса.
- Передача данных LAN-серверу (lease/handoff) — заглушка.
- Сверка контракта Android/iOS с сериализаторами `/app/`.
- Разбить `src/apps/race/views.py` (~3000 строк).
- Мусор в корне репозитория: `ORDER_*.json`, `race12_splits.xlsx`, `jpeg_not_support/`.
