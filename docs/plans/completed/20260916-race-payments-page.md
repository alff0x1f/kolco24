# Страница платежей гонки (финансовый анализ)

## Overview

Админская страница `race/<slug>/payments/` — реестр всех платежей гонки плюс блоки итогов,
чтобы организатор видел, сколько денег собрано, сколько ушло в скидки по промокодам, сколько
принесли доп-услуги и как поступления распределены по дням.

Проблема: сегодня деньги видны только в `/admin/` по одному объекту `Payment` за раз, без
привязки к гонке и без агрегатов. Свести доход по гонке можно только вручную.

Интеграция: новая страница живёт в `apps.race` рядом с «Картой гонки» и «Кодами легенды» —
тот же гейт `can_edit_race`, та же кнопка в админском блоке на странице гонки. Ничего не пишет,
только читает: `Payment` + `PaymentExtra` + `RacePromo` + `VTBPayment`.

## Context (from discovery)

- файлы/компоненты: `src/apps/race/views.py` (`RaceAppDataView._load_and_authorize` — образец гейта),
  `src/apps/race/models.py` (`RaceExtra`/`PaymentExtra`/`RacePromo`), `src/website/models/models.py`
  (`Payment`, `Team`), `src/website/models/vtb.py` (`VTBPayment.status_changed_at`, `order_id`),
  `src/website/urls.py`, `src/templates/race/race_page.html:148-157` (блок админских кнопок)
- паттерны: `RaceTeamsView` + `src/templates/race/teams.html:57,73-74` + `src/static/js/teams.js:251`
  — **пустой `<tbody>`**, строки рисует JS из JSON-острова; `_safe_json` (`views.py:51-59`)
  для безопасной вставки JSON в `<script type="application/json">`
- **`can_edit_race` (`src/apps/race/permissions.py`) НЕ даёт доступ суперюзеру** — проверяется
  только `RaceAdmin(role=ADMIN)`, вопреки прозе в CLAUDE.md. Эту страницу пишем под фактическое
  поведение; расширять `can_edit_race` здесь нельзя — это молча откроет легенду, коды, карту,
  app-data и протоколы
- **`_safe_json` — голый `json.dumps` без `default=`**: `datetime` в строке уронит страницу
  `TypeError`. Все даты сериализуем на сервере в строки
- `USE_TZ = True`, `TIME_ZONE = "Asia/Yekaterinburg"` — дни группируем по серверной локальной
  дате, не по браузерной
- зависимости: `Payment.team` nullable; связь с гонкой только через `team.category2.race`;
  `PaymentExtra` хранит снапшот `count` + `unit_price`; `Payment.payment_amount` уже net
  скидки, `discount_amount` — снапшот скидки
- тесты: `src/apps/race/tests.py`, pytest-стиль (`@pytest.mark.django_db`, фикстуры
  `client`/`django_user_model`), не `TestCase`

## Development Approach

- **testing approach**: Regular (код, затем тесты в той же задаче)
- выполнять задачи по одной, полностью
- **CRITICAL: каждая задача обязана включать новые/обновлённые тесты** для своего кода
- **CRITICAL: все тесты проходят до начала следующей задачи**
- **CRITICAL: обновлять этот файл, если по ходу меняется объём работ**
- команда тестов: `uv run pytest src/apps/race/tests.py` (быстрее — с `--reuse-db`)
- перед коммитом: `make format && make lint`
- работать в новой ветке, не в `master`

## Testing Strategy

- **unit-тесты**: обязательны в каждой задаче (см. выше)
- **e2e-тестов в проекте нет** (Playwright/Cypress отсутствуют) — UI проверяется
  Django-тест-клиентом: код ответа, наличие ключевых элементов и корректность JSON-острова
- JS-тестов в проекте нет; логика `payments.js` проверяется вручную (см. Post-Completion)

## Progress Tracking

- отмечать выполненное `[x]` сразу
- новые задачи — с префиксом ➕
- блокеры — с префиксом ⚠️

## Solution Overview

Вариант A из брейншторма: **один серверный рендер + клиентские фильтры**. На гонку приходится
300–400 платежей — весь срез спокойно помещается в страницу.

1. `apps/race/finance.py` — единственный источник данных: `payment_rows(race)` возвращает
   список плоских словарей. Его же использует CSV-экспорт, поэтому страница и выгрузка не могут
   разойтись.
2. `RacePaymentsView` отдаёт строки одним JSON-островом; `<tbody>` в шаблоне пустой.
3. `payments.js` рисует тело таблицы и считает все итоги под текущим фильтром.
4. `RacePaymentsExportView` отдаёт CSV по тем же строкам.

Ключевые решения:

- **Участие считается остатком**: `fee_sum = payment_amount + discount_amount − extras_sum`.
  Не `paid_for × cost_per_person` — при промокоде и доп-услугах `payment_amount` намеренно
  расходится с этим произведением (см. `create_team_payment` в `apps/race/pricing.py`).
  Остаточный способ гарантирует сходимость разбивки с итогом на каждой строке.
- **`paid_at`**: для `done` — `vtb_payment.status_changed_at`, фолбэк `updated_at`; для
  черновиков — `created_at`. `Payment.payment_date` в коде нигде не заполняется — не используем.
  В строку кладём **две готовые строки**, посчитанные через `timezone.localtime`: `paid_at`
  («dd.mm.yy HH:MM», для отображения и сортировки) и `paid_date` (`YYYY-MM-DD`, ключ группировки
  по дням). `datetime` в JSON-остров не попадает никогда.
- **Возвраты (`cancel`) — отдельная плитка «Возвращено»**, не размазываются по разбивке дохода.
- **Удалённые команды (`Team.is_deleted`) включаются в срез**: `Payment.objects` не проходит
  через `TeamManager`, а деньги удалённой команды — реально полученные деньги. Решение
  осознанное, закрепляется тестом.
- **Доступ — только `RaceAdmin(role=ADMIN)`** (суперюзер как таковой доступа не имеет, см.
  Context). Тесты пиннят это, а не расширяют.
- **Итоги считает JS** под текущий фильтр, сервер отдаёт только строки. Без JS страница пустая —
  для админской страницы приемлемо, как и у `RaceTeamsView`.
- Библиотека графиков не подключается: динамика по дням — CSS-бары.

## Technical Details

**Queryset** (в `finance.py`):

```python
Payment.objects.filter(team__category2__race=race)
    .select_related("team", "team__category2", "promo", "vtb_payment")
    .prefetch_related("extras__race_extra")
    .order_by("-created_at")
```

Платежи с `team=None` в срез не попадают (их нельзя привязать к гонке).
Пожертвования (`DonateRequest` / `SPUTNIK_*`) — вне области.

**Форма строки** (`payment_rows`):

```python
{
  "id": int,
  "paid_at": str,                   # «16.09.26 14:32», localtime, "" если даты нет
  "paid_sort": str,                 # «2026-09-16T14:32», ключ сортировки
  "paid_date": str,                 # «2026-09-16», ключ группировки по дням
  "team_id": int, "team_name": str, # team.teamname или «без названия»
  "category": str,
  "status": str,                    # done | draft | cancel
  "status_label": str,              # «не оплачено» для draft/draft_with_info
  "paid_for": float, "cost_per_person": float,
  "promo": str,                     # код или ""
  "discount": int,                  # discount_amount
  "amount": float,                  # payment_amount
  "order_id": str,
  "extras": {code: count, ...},     # только ненулевые
  "extras_sum": int,
  "fee_sum": float,
  "extras_label": str,              # «Карты ×3, Трансфер ×2»
}
```

Инвариант на каждой строке: `fee_sum + extras_sum − discount == amount`. Это **алгебраическое
тождество** по определению `fee_sum`, так что тестом оно ничего не ловит. Проверять надо
конкретными числами (см. Task 1) и отдельно — что при отсутствии промокода и услуг `fee_sum`
совпадает с независимой формулой `paid_for × cost_per_person`.

`status_label`: `draft` и `draft_with_info` сливаются в «не оплачено» — для финансового
анализа разницы между ними нет.

**Итоги (JS, по видимым строкам):** Собрано (Σ `amount` по `done`), Возвращено (Σ по `cancel`,
минусом), Итого (разница), Платежей, Участников оплачено (Σ `paid_for` по `done`), Скидок
(Σ `discount` по `done`), Средний чек (Собрано ÷ Платежей).

**Разбивка дохода (JS, по `done`):** строка «Участие» = Σ `fee_sum`; затем по строке на каждый
`RaceExtra` из **каталога гонки** в его порядке (Σ count, Σ count × unit_price) — чтобы услуга
с нулевыми продажами тоже была видна; затем «Скидка» минусом; затем «Итого» = Σ `amount`.

**Динамика по дням (JS, по `done`):** группировка по серверному `paid_date`, горизонтальные
бары `width: %` от максимума, рядом сумма и число платежей.

**CSV:** `Content-Disposition: attachment; filename="payments-<slug>-<YYYY-MM-DD>.csv"`,
разделитель `;`, BOM `﻿` в начале (русский Excel). Плоские строки, **отдельная колонка на
каждую услугу** (не текстом). Фильтр статуса из query string `?status=done` (значения
`done` / `unpaid` / `cancel` / `all`, дефолт `done`, неизвестное значение → `done`); поиск и
сортировка не переносятся. Расхождение с существующим экспортом в `src/api/views/teams.py:51-55`
(`;` без BOM) намеренное: там выгрузку читают скриптом, тут — Excel.

## What Goes Where

- **Implementation Steps**: код, шаблоны, стили, JS, тесты
- **Post-Completion**: ручная проверка UI в браузере, сверка цифр с реальной гонкой

## Implementation Steps

### Task 1: Модуль finance.py — сбор строк платежей

**Files:**
- Create: `src/apps/race/finance.py`
- Modify: `src/apps/race/tests.py`

- [x] создать `payment_rows(race) -> list[dict]` с queryset из Technical Details
- [x] реализовать `_paid_at(payment)`: `done` → `vtb_payment.status_changed_at` с фолбэком
      `updated_at`, иначе `created_at`; результат прогнать через `timezone.localtime` и вернуть
      три строки `paid_at` / `paid_sort` / `paid_date` (никаких `datetime` в строке)
- [x] реализовать разложение суммы: `extras_sum = Σ count × unit_price`,
      `fee_sum = payment_amount + discount_amount − extras_sum`
- [x] собрать `status_label` (`draft` + `draft_with_info` → «не оплачено») и `extras_label`
      («Карты ×3, Трансфер ×2», только ненулевые); имя команды — из `team.teamname`
- [x] добавить `extras_catalog(race) -> list[dict]` — `RaceExtra` гонки в порядке каталога
      (`code`, `name`), для колонок CSV и строк разбивки
- [x] написать тест: платёж другой гонки не попадает в срез
- [x] написать тест: платёж с `team=None` не попадает в срез
- [x] написать тест: платёж удалённой команды (`is_deleted=True`) **попадает** в срез
- [x] написать тест арифметики на конкретных числах: команда с известной ценой, промокодом и
      двумя услугами → проверить точные значения `fee_sum`, `extras_sum`, `amount`
- [x] написать тест перекрёстной проверки: без промокода и услуг
      `fee_sum == paid_for × cost_per_person`
- [x] написать тест `paid_at`: берётся `vtb_payment.status_changed_at`; при его отсутствии —
      `updated_at`; формат — строка, а не `datetime`
- [x] написать тест меток: `draft_with_info` → «не оплачено»; `extras_label` даёт
      «Карты ×3, Трансфер ×2» и пропускает услуги с нулевым `count`
- [x] запустить `uv run pytest src/apps/race/tests.py` — должно пройти до задачи 2

### Task 2: RacePaymentsView и маршрут страницы

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [x] добавить `RacePaymentsView` в `src/apps/race/views.py`; гейт — через общий помощник
      `_load_race_for_admin` (аноним → `redirect(login?next=)`, не-админ → 403),
      пригодный и для экспорта из задачи 5
- [x] собрать контекст: `race`, `payments_json` и `extras_json` через существующий `_safe_json`
- [x] зарегистрировать путь `race/<slug:race_slug>/payments/` name=`race_payments` в
      `src/website/urls.py` (префикс `race_` — как у `race_map`/`race_app_data`)
- [x] написать тесты доступа по образцу тестов `legend_codes` (`tests.py:2937-3003`):
      аноним → 302 на login, посторонний юзер → 403, `RaceAdmin(ADMIN)` → 200,
      `RaceAdmin(MODERATOR)` → 403
- [x] написать тест, пиннящий текущее поведение: суперюзер без `RaceAdmin` → 403
- [x] написать тест: в `payments_json` попадают только платежи этой гонки, и это валидный JSON
- [x] запустить тесты — должно пройти до задачи 3

### Task 3: Шаблон страницы и серверная таблица

**Files:**
- Create: `src/templates/race/payments.html`
- Create: `src/static/css/payments.css` (пустая заглушка)
- Create: `src/static/js/payments.js` (пустая заглушка)
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/tests.py`

- [x] создать `payments.html`, extends `website/base-2.html`, обёртка `.race-payments`
      (не голый `.page` — `theme-2.css` его уже определяет)
- [x] отрисовать заголовок, панель фильтров (статус, поиск)
      и контейнеры под плитки итогов / разбивку / динамику (заполняет JS)
- [x] отрисовать шапку таблицы (Дата, Команда, Категория, Статус, Участников, Цена/чел,
      Доп-услуги, Промокод, Скидка, Сумма, Заказ) и **пустой `<tbody id="payRows">`** —
      строки рисует JS, как в `teams.html:57` + `teams.js:251`; дублировать разметку строки
      в шаблоне и в JS нельзя
- [x] добавить JSON-острова `<script id="payments-data" type="application/json">` и
      `<script id="extras-data" type="application/json">`
- [x] создать пустые `payments.css` / `payments.js` до подключения через `{% static %}` —
      `CacheBustingStaticFilesStorage` (`src/config/storage.py`) роняет рендер на отсутствующем
      файле
- [x] подключить `payments.css` через `{% block extra_head %}`, `payments.js` — в конце
- [x] добавить кнопку «Платежи» в админский блок `race_page.html` (рядом с «Карта гонки»)
- [x] написать тест: кнопка «Платежи» видна админу гонки и не видна обычному юзеру
- [x] запустить тесты — должно пройти до задачи 4
- ➕ кнопка «Скачать CSV» перенесена в задачу 5: её URL появляется вместе с вью экспорта

### Task 4: Клиентская логика — фильтры, сортировка, итоги

**Files:**
- Modify: `src/static/js/payments.js`
- Modify: `src/static/css/payments.css`

- [x] читать JSON-острова, при их отсутствии — тихо выйти (как в `teams.js`)
- [x] реализовать фильтр статуса (оплачено / не оплачено / возвраты / все, дефолт «оплачено»)
      и поиск по названию команды и `order_id`
- [x] реализовать сортировку по клику на заголовок колонки (по дате — через `paid_sort`),
      дефолт — дата вниз
- [x] рисовать тело таблицы из данных в пустой `<tbody>`
- [x] считать плитки итогов под текущим фильтром (Собрано, Возвращено, Итого, Платежей,
      Участников оплачено, Скидок, Средний чек)
- [x] считать разбивку дохода: «Участие», строка на каждый `RaceExtra` из каталога,
      «Скидка», «Итого»
- [x] считать динамику по дням по `paid_date` и рисовать CSS-бары (`width` % от максимума)
- [x] стили в `payments.css`, всё под `.race-payments`; таблица в контейнере с
      `overflow-x: auto`, проверить ширину ~400px
- [x] JS-тестов в проекте нет — проверить в браузере вручную (см. Post-Completion)
- [x] прогнать `uv run pytest src/apps/race/tests.py` — ничего не сломалось
- ➕ третий остров `#payments-config` с `teamUrlTemplate`: путь к команде строится через
      `reverse()`, а не хардкодом `/team/<id>/` в JS
- ➕ строка несёт `extras_money` (деньги по каждой услуге из её снапшота `unit_price`) —
      иначе разбивка восстанавливала бы доход услуги из общей суммы платежа

### Task 5: Экспорт CSV

**Files:**
- Modify: `src/apps/race/finance.py`
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [x] добавить в `finance.py` `csv_rows(rows, extras)` — плоские строки с отдельной колонкой
      на каждую услугу каталога
- [x] добавить `filter_rows(rows, status)` (`done` / `unpaid` / `cancel` / `all`, дефолт `done`,
      неизвестное значение → `done`) — **только для экспорта**: страница отдаёт все строки и
      фильтрует в JS
- [x] добавить `RacePaymentsExportView`, переиспользуя помощник гейта из задачи 2; отдавать
      `text/csv; charset=utf-8`, BOM `﻿`, разделитель `;`, имя `payments-<slug>-<YYYY-MM-DD>.csv`
- [x] зарегистрировать путь `race/<slug:race_slug>/payments/export/` name=`race_payments_export`
- [x] добавить кнопку «Скачать CSV» на страницу; JS держит в её ссылке текущий фильтр статуса
- [x] написать тесты: 200 для `RaceAdmin(ADMIN)`, 403 для постороннего, BOM в начале тела,
      число строк соответствует `?status=done`
- [x] написать тест: неизвестный `?status=` откатывается к `done`, не падает
- [x] запустить тесты — должно пройти до задачи 6

### Task 6: Verify acceptance criteria

- [x] проверить, что все требования из Overview реализованы
- [x] проверить краевые случаи: гонка без платежей, платёж без услуг, платёж без `vtb_payment`,
      услуга с нулевыми продажами (должна быть строкой в разбивке), платёж удалённой команды
- [x] сверить сходимость: Σ `fee_sum` + Σ `extras_sum` − Σ `discount` == Σ `amount`
- [x] прогнать весь набор: `uv run pytest` — 1239 тестов зелёные
- [x] `make format && make lint`

### Task 7: [Final] Update documentation

- [x] дописать в `CLAUDE.md` абзац про страницу платежей в разделе `apps.race`
      (URL-имена, гейт, `finance.py` как единый источник, правило «участие — остаток»,
      включение удалённых команд, BOM в CSV)
- [x] исправить в `CLAUDE.md` утверждение «`can_edit_race` — superuser, или RaceAdmin с
      role=ADMIN»: суперюзерной ветки в `permissions.py` нет (правки в описаниях
      `RaceEditView` и `CanEditRaceLegend`)
- [x] README трогать не нужно
- [x] переместить план в `docs/plans/completed/`

## Post-Completion

*Требует ручных действий — без чекбоксов*

**Ручная проверка:**
- открыть страницу на реальной гонке с промокодами и доп-услугами, сверить «Собрано» с
  выпиской VTB за тот же период
- проверить фильтры, поиск, сортировку и пересчёт итогов в браузере
- скачать CSV и открыть в русском Excel — кириллица и разделители должны быть корректны
- проверить вёрстку на телефоне (~400px): таблица скроллится по горизонтали, страница — нет

**Внешние системы:** изменений не требуется — страница только читает.
