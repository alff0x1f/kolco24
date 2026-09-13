# Промокоды на оплату участия в гонке

## Overview

Добавить промокоды к оплате участия: скидка в процентах (например 40%) или фиксированной суммой
(например −1000 ₽), с общим лимитом использований. Промокод привязан к одной гонке и управляется
организатором на странице редактирования гонки — рядом с категориями, ценовыми периодами и доп-услугами.

Что это решает: сейчас единственный рычаг цены — `RacePriceTier` (ладдер по датам), общий для всех.
Нет способа дать скидку конкретной аудитории (партнёры, волонтёры, промо-акция) без ручных операций
с платежами.

Интеграция: скидка входит в существующую формулу заряда `apps/race/pricing.py:compute_team_charge`
и уменьшает сумму, которая уходит в ВТБ. Зачисление мест (`paid_for`) и доп-услуг (`PaymentExtra`)
не меняется — они и так явные снапшоты, а не обратный пересчёт из суммы.

## Context (from discovery)

Файлы и компоненты, которых касается задача:

- `src/apps/race/models.py` — `RaceExtra`/`TeamExtra`/`PaymentExtra`, образец для новой модели.
- `src/apps/race/pricing.py` — `compute_team_charge`, `upsert_team_extras`, `create_team_payment`
  (единственный источник правды по формуле заряда).
- `src/website/models/models.py` — `Payment` (`STATUS_DRAFT`/`STATUS_DONE`, `paid_for`,
  `payment_amount`, legacy `payment_with_discount` от удалённой в `0063` модели `Coupons`).
- `src/website/models/race.py` — `RESERVATION_TTL = timedelta(minutes=20)`, `Race.reserved_people`
  (образец «живой брони» по draft-платежам).
- `src/website/management/commands/check_vtb_payments.py` — `_settle_race_payment`/`_credit_extras`,
  зачисление подтверждённого платежа.
- `src/website/forms.py` — `TeamForm` (динамические поля `extra_<code>`, защитный резолв гонки).
- `src/website/views/views_.py` — `AddTeam.post`, `build_team_form_context` (конфиг-остров
  `teamFormConfig`); `src/website/views/team.py` — `EditTeamView.post`.
- `src/apps/race/views.py` — `RaceEditView.post`, `_validate_extra_rows`, `_reconcile_extras`,
  `_existing_extras`; `src/apps/race/forms.py` — `RaceForm.extras_json`.
- `src/templates/race/race_form.html`, `src/static/js/race_form.js`, `src/static/css/race_form.css`.
- `src/templates/website/add_team.html`, `edit_team.html`, `src/static/js/team-form.js`,
  `src/static/css/team-form.css`.
- Тесты: `src/apps/race/tests.py`, `src/website/tests.py` (pytest-стиль, `@pytest.mark.django_db`).

Найденные паттерны:

- Справочники гонки постятся одним скрытым JSON-полем и реконсайлятся внутри одного
  `transaction.atomic()` (`categories_json` / `price_tiers_json` / `extras_json`).
- У доп-услуг мягкая политика удаления: используемая строка деактивируется, а не удаляется;
  `PROTECT` на FK — подстраховка.
- Клиент зеркалит серверную формулу заряда; в `pricing.py` и `team-form.js` стоят перекрёстные
  комментарии об этом правиле.
- В `apps/race` нет `admin.py` — весь CRUD справочников живёт на странице редактирования гонки.

Зависимости: кросс-аппные FK между `website` и `race_app` уже есть в обе стороны
(`apps/race/0001` зависит от `website/0072`), новая `website`-миграция зависит от новой
`race`-миграции — цикла в графе нет.

## Development Approach

- **testing approach**: Regular (сначала код, тесты в той же задаче до перехода к следующей).
- работать в ветке (`feat/promo-codes`), **не коммитить в master**.
- перед каждым коммитом: `make format && make lint`.
- тесты: `uv run pytest` (для итераций `uv run pytest --reuse-db`).
- каждая задача завершена только когда её тесты написаны и весь прогон зелёный.
- обновлять этот файл по ходу: `[x]` сразу по факту, ➕ для новых задач, ⚠️ для блокеров.

## Testing Strategy

- **unit/интеграционные тесты**: обязательны в каждой задаче; pytest-функции с
  `@pytest.mark.django_db`, фикстуры `client` / `django_user_model` — как в существующих
  `src/apps/race/tests.py` и `src/website/tests.py`.
- **e2e**: в проекте нет браузерных e2e-тестов. Поведение JS покрывается косвенно —
  тестом-зеркалом конфиг-острова (`test_config_island_mirrors_compute_team_charge`), который
  расширяется кейсом со скидкой.
- каждая задача покрывает и успешный путь, и ошибочные/краевые (истёкший лимит, битый код,
  скидка больше взноса, нулевой платёж).

## Progress Tracking

- `[x]` сразу после выполнения пункта, не пачкой.
- ➕ — задача, найденная по ходу.
- ⚠️ — блокер или отклонение от плана.
- при изменении объёма — править план, а не держать расхождение в голове.

## Solution Overview

Выбранный подход (вариант A из брейншторма): **учёт использований выводится из `Payment`**, без
денормализованного счётчика и без отдельной таблицы `PromoUse`.

Ключевая идея: правило «одна команда = одно использование» превращает «сколько раз использован код»
в «сколько **разных команд** его заняли». Занятой считается команда, у которой есть платёж с этим
промокодом в статусе `done` **или** «живой» `draft` (моложе `RESERVATION_TTL = 20 мин`) — ровно тот же
паттерн брони, что уже применён к местам в `Race.reserved_people`.

Почему так:

- нет состояния, которое может разойтись с реальностью; идемпотентность зачисления не требует
  дополнительных флагов — счётчик пересчитывается запросом;
- 20-минутная бронь закрывает окно «ушёл на оплату со скидкой, а квоту забрали» без крон-задач:
  заброшенный draft освобождает квоту сам, отменённый (`cancel`) — сразу;
- конкурентную заявку на последнюю квоту сериализует `select_for_update()` на строке промокода
  внутри уже существующего `transaction.atomic()` в `create_team_payment`.

Скидка действует **только на взнос за участие**: `fee = max(0, (ucount − paid_people) × current_price)`.
Доп-услуги (карты, трансфер, завтрак) идут по полной цене — у них есть себестоимость.

Побочный эффект, который надо обработать: скидка может обнулить сумму (100% или фикс ≥ взноса).
Сегодня `cost == 0` в `create_team_payment` означает «платить нечего» и возвращает `None`; теперь
может означать «платить нечего, но места зачислить надо». Для этого логика зачисления выносится из
management-команды в `apps/race/settlement.py:settle_payment(payment)` и переиспользуется обоими
путями.

## Technical Details

### Модель `RacePromo` (`src/apps/race/models.py`)

```
race           FK website.Race, related_name="promos", CASCADE
code           CharField(32)      # .strip().upper() в save()
discount_type  CharField(8)       # "percent" | "fixed"
value          IntegerField
max_uses       IntegerField(0)    # 0 = без лимита
is_active      BooleanField(True)
comment        CharField(255, blank, default="")
order          IntegerField(0)
Meta: unique_together ("race", "code"), ordering ["order", "id"]
```

`discount_for(fee) -> int`: `percent` → `fee * value // 100`, `fixed` → `min(value, fee)`;
результат зажат в `0 … fee` (скидка никогда не уводит итог в минус и не трогает доп-услуги).

### Поля `Payment` (`src/website/models/models.py`)

```
promo            FK apps.race.RacePromo, null=True, blank=True, on_delete=PROTECT,
                 related_name="payments"
discount_amount  IntegerField(0)   # снапшот применённой скидки, ₽
```

`payment_amount` остаётся реально списываемой суммой (уже со скидкой), поэтому `team.paid_sum`
продолжает означать «сколько денег получено». Legacy-поле `payment_with_discount` не трогаем: сейчас
`create_team_payment` пишет в него ту же величину, что и в `payment_amount`
(`src/apps/race/pricing.py:94`), и это остаётся так — то есть после изменения оно тоже хранит сумму
**со скидкой**, а не взнос до неё. Смысл старого поля с историей платежей менять рискованнее, чем
завести явное `discount_amount`.

### `src/apps/race/promo.py`

```python
class PromoError(Exception):      # .key: not_found | inactive | limit_reached | already_used
class PromoUnavailable(Exception) # квота ушла между сабмитом и созданием платежа

def occupied_team_ids(promo) -> set[int]
def resolve_promo(race, code, team) -> RacePromo   # бросает PromoError
```

`occupied_team_ids`:

```python
cutoff = timezone.now() - RESERVATION_TTL
qs = Payment.objects.filter(promo=promo).exclude(team__isnull=True).filter(
    Q(status=Payment.STATUS_DONE)
    | Q(status=Payment.STATUS_DRAFT, created_at__gt=cutoff)
)
return set(qs.values_list("team_id", flat=True))
```

`exclude(team__isnull=True)` обязателен: `Payment.team` — `null=True`, и без него в множество попадёт
`None`, из-за чего несохранённая команда (`Team()` на add-флоу) выглядела бы «уже занявшей» код и
проскакивала бы мимо проверки лимита.

`STATUS_DRAFT_WITH_INFO` в квоте **не учитывается** осознанно — точно так же, как в
`Race.reserved_people` (`src/website/models/race.py:124`): этим статусом помечались платежи легаси
ручной сверки, живого флоу за ним нет. Зафиксировать это в docstring модуля.

Порядок проверок в `resolve_promo`: кода нет в гонке → `not_found`; `is_active=False` → `inactive`;
у команды есть `DONE`-платёж с этим кодом → `already_used`; команды нет в `occupied` и
`max_uses and len(occupied) >= max_uses` → `limit_reached`. Команда со своим живым draft проходит:
повторный сабмит не съедает вторую квоту и не блокирует сам себя.

**Несохранённая команда**: на add-флоу `TeamForm.team` — это `Team()` без `pk`
(`src/website/forms.py:65`). Django 4.2 молча компилирует `filter(team=<unsaved>)` в
`WHERE team_id IS NULL`, поэтому `resolve_promo` должен явно пропускать проверку `already_used`
при `team is None or team.pk is None`.

Тексты ошибок (единый словарь в модуле, используется формой и AJAX-эндпоинтом):

| key | текст |
| --- | --- |
| `not_found` | Промокод не найден |
| `inactive` | Промокод больше не действует |
| `limit_reached` | Лимит промокода исчерпан |
| `already_used` | Ваша команда уже использовала этот промокод |

### Формула заряда

`compute_team_charge(team, race, promo=None) -> (total, lines, discount)`:

```python
fee = max(0, int((int(team.ucount) - team.paid_people) * race.current_price))
discount = promo.discount_for(fee) if promo else 0
total = max(0, fee - discount + Σ extras)
```

`int()` вокруг `fee` обязателен: `Team.paid_people` — `FloatField`
(`src/website/models/models.py:154`), поэтому без приведения `fee` и вся арифметика скидки
становятся float. Сейчас код приводит к int только в самом конце (`return max(0, int(total)), lines`,
`src/apps/race/pricing.py:53`), но `discount_amount` — `IntegerField`, а JS-зеркало считает
`Math.floor`, так что округление должно происходить **до** применения скидки, иначе клиент и сервер
разойдутся. По той же причине `discount_for` приводит `fee` к `int` у себя на входе.

Возврат становится трёхэлементным — поправить все вызовы, включая тест-зеркало
`src/website/tests.py:2385`.

### Поток запроса

1. `team-form.js` → `GET race/<slug>/promo/check/?code=<code>&team_id=<id>` (`promo_check`),
   ответ `{ok: true, code, type, value}` либо `{ok: false, error}`.
   Эндпоинт **не считает итог** — только валидирует код; сумму считает JS по формуле-зеркалу.
   **GET, а не POST**: проверка ничего не пишет в БД, а в `src/static/js/` сейчас нет ни одного
   POST-запроса и, соответственно, хелпера для CSRF-токена (`CsrfViewMiddleware` включён,
   `src/config/settings.py:134`). GET снимает вопрос CSRF целиком и повторяет уже принятый в
   проекте приём JSON-GET (`race_map_positions`, `race_map_track`).
2. Сабмит формы: скрытый `promo_code` → `TeamForm.clean_promo_code` → `self.promo`
   (`PromoError` → `ValidationError` поля).
3. `AddTeam.post` / `EditTeamView.post` → `create_team_payment(request, team, race, promo=form.promo)`.
4. `create_team_payment` внутри `transaction.atomic()`: `select_for_update()` на строке промокода,
   перепроверка доступности, затем `Payment(promo=..., discount_amount=..., payment_amount=total)`.
   Квота ушла → `PromoUnavailable` → вьюха перерисовывает форму с ошибкой.
5. `total == 0` и есть что зачислять (`paid_for > 0` или дельта доп-услуг) →
   `Payment(status=draft, payment_amount=0, promo, discount_amount)` + `PaymentExtra` +
   `settle_payment(payment)`, который сам переведёт платёж в `done`. Создавать сразу со
   `status=done` **нельзя**: первая строка `settle_payment` — гард идемпотентности
   `if payment.status == STATUS_DONE: return False`, и зачисления не произойдёт вообще.
   `create_team_payment` в этой ветке возвращает `None`, как и раньше, — вызывающая вьюха сама
   уводит на свой success-URL (у add и edit они разные).
6. Подтверждение обычного платежа: `check_vtb_payments` → `settle_payment(payment)` (логика та же,
   просто переехала).

### Принятые краевые случаи

- Команда, оплатившая со скидкой, при доплате за новых участников платит по полной (`already_used`) —
  прямое следствие правила «1 раз на команду».
- Удалённая (`is_deleted`) команда продолжает держать квоту: скидка реально была выдана.
  Освобождается ручной отменой платежа.
- Ручная правка `Payment.status` в админке сразу меняет доступность кода — следствие derived-счётчика.
- `paid_people`, `paid_for`, `PaymentExtra` от скидки не зависят; результаты и протокол не затронуты.
- Перебор кодов авторизованным пользователем — принятый риск: `resolve_promo` ничего не пишет в БД,
  квота расходуется только платежом.

## What Goes Where

- **Implementation Steps** — всё, что делается в этом репозитории: модели, миграции, сервисы, вьюхи,
  шаблоны, JS/CSS, тесты, документация.
- **Post-Completion** — ручная проверка на стенде, заполнение промокодов организаторами, наблюдение
  за первым реальным применением.

## Implementation Steps

### Task 1: Модель `RacePromo` и миграция

**Files:**
- Modify: `src/apps/race/models.py`
- Create: `src/apps/race/migrations/0005_racepromo.py` (последняя сейчас — `0004_protocol_updated_at.py`)
- Modify: `src/apps/race/tests.py`

- [x] добавить модель `RacePromo` рядом с `RaceExtra` (поля, `unique_together`, `ordering`, `__str__`)
- [x] нормализация `code` в `save()` (`.strip().upper()`), константы типов `PERCENT`/`FIXED` с `choices`
- [x] метод `discount_for(fee)`: `int(fee)` на входе, зажим результата в `0 … fee`, округление вниз
      для процента
- [x] сгенерировать миграцию: `uv run python src/manage.py makemigrations race_app`
- [x] написать тесты `discount_for`: процент, фикс, фикс больше взноса, нулевой `fee`, округление
      вниз, float на входе (`paid_people` — `FloatField`) даёт `int` на выходе
- [x] написать тест нормализации кода в `save()` и `unique_together` внутри гонки
- [x] прогнать тесты — должны пройти до перехода к задаче 2

### Task 2: Поля `promo` и `discount_amount` на `Payment`

**Files:**
- Modify: `src/website/models/models.py`
- Create: `src/website/migrations/0093_payment_promo.py` (последняя сейчас —
  `0092_newspost_publication_fields.py`)
- Modify: `src/website/tests.py`

- [x] добавить `promo` (FK `race_app.RacePromo`, `null`, `blank`, `PROTECT`, `related_name="payments"`)
      и `discount_amount` (`IntegerField(0)`) в `Payment`
- [x] комментарий у полей: `payment_amount` — уже со скидкой; legacy `payment_with_discount` не трогаем
- [x] сгенерировать миграцию, проверить, что в `dependencies` попала новая миграция `race_app`
- [x] прогнать `uv run python src/manage.py migrate` на локальной БД — применяется без ошибок
- [x] написать тест: `Payment` создаётся без промо (дефолты `None`/`0`) и с промо
- [x] написать тест: удаление `RacePromo` с платежом падает `ProtectedError`
- [x] прогнать тесты — должны пройти до перехода к задаче 3

### Task 3: Сервис `apps/race/promo.py` — резолв и квота

**Files:**
- Create: `src/apps/race/promo.py`
- Modify: `src/apps/race/tests.py`

- [x] `PromoError` (с `.key`), `PromoUnavailable`, словарь русских текстов по ключам
- [x] `occupied_team_ids(promo)` — `DONE` + живые `DRAFT` моложе `RESERVATION_TTL`, `set` team_id,
      обязательный `.exclude(team__isnull=True)` (иначе `None` попадёт в множество)
- [x] `resolve_promo(race, code, team)` в порядке `not_found → inactive → already_used → limit_reached`;
      при `team is None or team.pk is None` (add-флоу, несохранённый `Team()`) проверка
      `already_used` пропускается, лимит проверяется как для новой команды
- [x] docstring модуля: почему derived-счётчик, как бронь совпадает с `Race.reserved_people`,
      почему `STATUS_DRAFT_WITH_INFO` намеренно не входит в квоту
- [x] тесты на все 4 ключа ошибок и на успешный резолв
- [x] тест: резолв для несохранённой команды (`Team()`) не падает, не считается занятым и упирается
      в лимит наравне с остальными
- [x] тесты: свой живой draft проходит и не съедает вторую квоту; draft старше 20 мин освобождает
      квоту; `cancel` освобождает сразу; `max_uses=0` — без лимита
- [x] прогнать тесты — должны пройти до перехода к задаче 4

### Task 4: Скидка в `compute_team_charge` и правка вызовов

**Files:**
- Modify: `src/apps/race/pricing.py`
- Modify: `src/apps/race/tests.py`
- Modify: `src/website/tests.py`

- [x] `compute_team_charge(team, race, promo=None)` возвращает `(total, lines, discount)`;
      `fee = max(0, int(...))` вынесен отдельным слагаемым, скидка применяется только к нему
- [x] обновить docstring модуля (формула со скидкой + перекрёстная ссылка на `team-form.js`)
- [x] поправить внутренний вызов в `create_team_payment` под новую распаковку
- [x] поправить существующие тесты `src/apps/race/tests.py`, распаковывающие два значения
- [x] поправить распаковку в `test_config_island_mirrors_compute_team_charge`
      (`src/website/tests.py:2385`) — иначе полный прогон красный уже на гейте этой задачи
- [x] новые тесты: процент, фикс больше взноса (итог не отрицательный), доп-услуги не дешевеют,
      доплата считается от неоплаченной части, `promo=None` даёт прежний результат,
      дробный `paid_people` не порождает float в `total`/`discount`
- [x] прогнать тесты — должны пройти до перехода к задаче 5

### Task 5: Вынос зачисления в `apps/race/settlement.py`

**Files:**
- Create: `src/apps/race/settlement.py`
- Modify: `src/website/management/commands/check_vtb_payments.py`
- Modify: `src/apps/race/tests.py`

- [x] перенести `_settle_race_payment` → `settle_payment(payment) -> bool` и `_credit_extras` →
      `credit_extras(team, payment)` без изменения логики (гард `status == STATUS_DONE`,
      `transaction.atomic()`, авто `SOLD_OUT`, атомарный инкремент `TeamExtra`)
- [x] в команде оставить тонкие обёртки-вызовы `settle_payment` (публичный API команды не меняется)
- [x] docstring: идемпотентность живёт в статусе платежа, функция зовётся из двух мест
- [x] тесты: `settle_payment` зачисляет места и доп-услуги, повторный вызов ничего не дублирует,
      флип `OPEN → SOLD_OUT` при достижении лимита
- [x] убедиться, что существующие тесты команды `check_vtb_payments` зелёные
- [x] прогнать тесты — должны пройти до перехода к задаче 6

### Task 6: Промокод в `create_team_payment` и нулевой платёж

**Files:**
- Modify: `src/apps/race/pricing.py`
- Modify: `src/apps/race/tests.py`

- [x] `create_team_payment(request, team, race, promo=None)`: внутри `transaction.atomic()`
      `RacePromo.objects.select_for_update().get(pk=promo.pk)` + перепроверка доступности,
      при отказе — `PromoUnavailable`
- [x] писать `promo` и `discount_amount` в `Payment`, в ВТБ уходит сумма со скидкой
- [x] ветка `total == 0`: если есть что зачислять (`paid_for > 0` или дельта доп-услуг) — создать
      `Payment(status=draft, payment_amount=0, promo, discount_amount)` + `PaymentExtra` и вызвать
      `settle_payment` (он сам переведёт в `done`; создавать сразу `done` нельзя — гард
      идемпотентности не зачислит ничего). Возврат в этой ветке — `None`, как и раньше;
      в ВТБ ничего не уходит
- [x] тесты: снапшоты `promo`/`discount_amount`, сумма заказа ВТБ со скидкой, `PromoUnavailable`
      когда квоту забрали между резолвом и платежом (мокать ВТБ как в `src/website/tests.py:788`,
      `patch("apps.race.pricing.VTBClient")`)
- [x] тесты нулевого платежа: возвращается `None`, `Payment` в БД со `status=done` и
      `payment_amount=0`, `paid_people` и `count_paid` зачислены, квота промокода занята,
      обращений к ВТБ не было; «платить нечего и зачислять нечего» по-прежнему даёт `None`
      и не создаёт платёж
- [x] прогнать тесты — должны пройти до перехода к задаче 7

### Task 7: Поле `promo_code` в `TeamForm`

**Files:**
- Modify: `src/website/forms.py`
- Modify: `src/website/views/views_.py`
- Modify: `src/website/tests.py`

- [x] добавить `promo_code = forms.CharField(required=False, widget=forms.HiddenInput)`
- [x] в `__init__` инициализировать `self.promo = None`; резолвить только когда гонка разрешилась
      (та же защитная ветка, что для `extras`)
- [x] `clean_promo_code`: пусто → `None`; иначе `resolve_promo`, `PromoError` → `ValidationError`
      с текстом по ключу
- [x] **в этой же задаче** исключить `promo_code` из `team_fields` в `AddTeam.post`
      (`src/website/views/views_.py:448`) — `TeamForm` не `ModelForm`, а `cleaned_data` расплющивается
      в `Team.objects.create(**team_fields)`, так что без этой правки каждый тест регистрации падает
      с `TypeError: Team() got unexpected keyword argument 'promo_code'`.
      `EditTeamView.post` присваивает поля явно и не затронут
- [x] тесты: пустой код → `form.promo is None`; несуществующий/выключенный/исчерпанный код → ошибка
      поля с нужным текстом; валидный код → `form.promo` заполнен
- [x] тест: при битом `race_id` форма не падает и промо не резолвится
- [x] тест: существующие сценарии `AddTeam` (регистрация без промокода) остаются зелёными
- [x] прогнать тесты — должны пройти до перехода к задаче 8

### Task 8: Передача промокода из вьюх команды

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/views/team.py`
- Modify: `src/website/tests.py`

- [x] `AddTeam.post`: звать `create_team_payment(request, team, race, promo=form.promo)`
      (фильтрация `promo_code` уже сделана в задаче 7)
- [x] `EditTeamView.post`: то же самое
- [x] обе вьюхи ловят `PromoUnavailable` и перерисовывают форму с ошибкой формы
      («Промокод больше недоступен»), не уводя пользователя на оплату
- [x] тесты `AddTeam`: регистрация с промокодом создаёт платёж со скидкой; без промокода поведение
      прежнее; `PromoUnavailable` возвращает форму с ошибкой (200, платёж не создан)
- [x] тесты `EditTeamView`: доплата после оплаты со скидкой идёт по полной цене (`already_used`)
- [x] прогнать тесты — должны пройти до перехода к задаче 9

### Task 9: JSON-эндпоинт `promo_check`

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [x] вьюха `PromoCheckView` (**GET**, без CSRF-возни): гонка по `race_slug` (404, только
      опубликованная), параметры `code` и необязательный `team_id`
- [x] аутентификация ручной веткой, как в соседних вьюхах `apps/race` (`_load_and_authorize`,
      `src/apps/race/views.py:747`), но вместо редиректа — `403 {"ok": false, "error": ...}`,
      чтобы AJAX не получал HTML-страницу логина
- [x] `team_id` резолвится строго в команду этой гонки, владельцем которой является
      `request.user` (либо пользователь проходит `can_edit_race`) — как `EditTeamView.get_team`;
      чужая команда или команда другой гонки → как будто `team_id` не передан
- [x] ответ `{ok: true, code, type, value}` либо `{ok: false, error}`; пустой `code` → `{ok: false}`
- [x] маршрут `race/<slug:race_slug>/promo/check/`, имя `promo_check` в `src/website/urls.py`
- [x] тесты: валидный код, каждая ошибка резолва, чужой `team_id`, `team_id` другой гонки,
      аноним (403 JSON), пустой `code`, несуществующая/неопубликованная гонка
- [x] тест: эндпоинт не создаёт и не меняет ни одной строки в БД
- [x] прогнать тесты — должны пройти до перехода к задаче 10

### Task 10: Ввод промокода на форме команды (конфиг + шаблоны + JS)

**Files:**
- Modify: `src/website/views/views_.py` (`build_team_form_context`)
- Modify: `src/templates/website/add_team.html`
- Modify: `src/templates/website/edit_team.html`
- Modify: `src/static/js/team-form.js`
- Modify: `src/static/css/team-form.css`
- Modify: `src/website/tests.py`

- [x] в конфиг-остров `teamFormConfig` добавить `promo: {code, type, value} | null` и
      `promoCheckUrl`; при перерисовке формы с ошибкой применённый код сохраняется
- [x] в оба шаблона добавить блок ввода под доп-услугами: поле, кнопка «Применить», место под
      ошибку, скрытый `promo_code`; стили в `team-form.css`
- [x] в `team-form.js`: GET-запрос к `promoCheckUrl` (`?code=…&team_id=…`, без CSRF-заголовка),
      состояние промо, кнопка «Убрать», строка «Промокод XXX: −N ₽» в разбивке итога,
      обработка сетевой ошибки/403
- [x] формула-зеркало в `render()`: `fee = max(0, ucount×COST − PAID_PEOPLE×COST)`,
      `discount = type === "percent" ? Math.floor(fee * value / 100) : Math.min(value, fee)`,
      итог `max(0, fee − discount + Σ extras)`; смена размера/категории пересчитывает скидку,
      код не сбрасывается
- [x] обновить перекрёстные комментарии о зеркале в `team-form.js` и `apps/race/pricing.py`
- [x] расширить `test_config_island_mirrors_compute_team_charge` кейсом со скидкой (процент и фикс)
- [x] тесты: конфиг-остров содержит `promo` после ошибки валидации формы
- [x] прогнать тесты — должны пройти до перехода к задаче 11

### Task 11: Реконсайл промокодов в `RaceEditView`

**Files:**
- Modify: `src/apps/race/forms.py`
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [ ] `RaceForm.promos_json = CharField(required=False, widget=HiddenInput)` (пустой payload —
      не ошибка, как у доп-услуг)
- [ ] `_validate_promo_rows`: `code` `^[A-Z0-9_-]{2,32}$` (в верхний регистр), уникален в гонке;
      `discount_type` из двух значений; `value` — percent `1…100`, fixed `> 0`; `max_uses ≥ 0`;
      `comment` ≤ 255
- [ ] `_reconcile_promos`: матч по `id`, затем по `code`; `order = index`; отсутствующая строка
      удаляется только если на неё нет `Payment`, иначе `is_active=False`; `ProtectedError` —
      подстраховка (та же мягкая политика, что у `_reconcile_extras`)
- [ ] `code` read-only после сохранения (правка кода существующей строки игнорируется)
- [ ] `_existing_promos(race)` для контекста: поля строки + `used` (из `occupied_team_ids`) и
      `max_uses`; сейчас это один запрос на промокод — оставить комментарий об N+1 (кодов единицы)
- [ ] подключить парсинг/валидацию/реконсайл в `RaceEditView.post` внутри существующего
      `transaction.atomic()` и в ветку перерисовки с ошибками; добавить `promos_data`/`promo_errors`
      в `RaceEditView._build_context` (`src/apps/race/views.py:765`)
- [ ] тесты: создание, правка, деактивация используемой строки, удаление неиспользуемой,
      попытка сменить `code` сохранённой строки, все ветки валидации, ошибки не сохраняют ничего
- [ ] прогнать тесты — должны пройти до перехода к задаче 12

### Task 12: Блок «Промокоды» на странице редактирования гонки

**Files:**
- Modify: `src/templates/race/race_form.html`
- Modify: `src/static/js/race_form.js`
- Modify: `src/static/css/race_form.css`
- Modify: `src/apps/race/tests.py`

- [ ] секция «Промокоды» после доп-услуг: заголовок, заголовки колонок, контейнер строк,
      кнопка добавления, скрытый `promos_json`, JSON-остров `#promos-data`
- [ ] в `race_form.js` — построение строк из острова, добавление/удаление/деактивация,
      сериализация в `promos_json` перед сабмитом (по образцу блока доп-услуг)
- [ ] селектор типа скидки меняет подпись значения («%» / «₽»), клиентская валидация диапазонов
- [ ] read-only колонка «использовано N из M» (`M` = «∞» при `max_uses = 0`), `code` заблокирован
      у сохранённых строк
- [ ] стили секции в `race_form.css` в стиле соседних блоков
- [ ] тесты вьюхи: страница рендерит остров `#promos-data` с ожидаемыми строками и счётчиком `used`
- [ ] прогнать тесты — должны пройти до перехода к задаче 13

### Task 13: Verify acceptance criteria

- [ ] сквозной сценарий на локальном стенде: создать промокод → зарегистрировать команду со скидкой
      → подтвердить платёж → повторная попытка того же кода той же командой даёт `already_used`
- [ ] проверить исчерпание лимита разными командами и освобождение квоты по истечении 20 мин
- [ ] проверить 100%-скидку: платёж `done` с суммой 0, места и доп-услуги зачислены
- [ ] проверить, что без промокода все прежние пути (оплата, доплата, нулевой заряд) не изменились
- [ ] `make format && make lint` — чисто
- [ ] полный прогон `uv run pytest` — зелёный

### Task 14: [Final] Update documentation

- [ ] добавить в `CLAUDE.md` секцию «Промокоды» рядом с «Team add-ons / доп-услуги»: модель,
      derived-учёт с 20-минутной бронью, база скидки (только взнос), нулевой платёж, мягкая политика
      удаления, принятые краевые случаи
- [ ] отразить в `CLAUDE.md` переезд зачисления в `apps/race/settlement.py:settle_payment`
- [ ] перекрёстная ссылка на промокоды в шапке `src/apps/race/pricing.py`
- [ ] перенести этот план в `docs/plans/completed/`

## Post-Completion

*Требует ручных действий вне репозитория — без чекбоксов.*

**Ручная проверка:**

- прогон на стенде реального платежа ВТБ со скидкой: сумма в банковском интерфейсе совпадает с
  показанной на форме; после подтверждения `check_vtb_payments` места зачисляются корректно.
- проверить вёрстку блока промокода на мобильном (форма команды) и блока «Промокоды» на странице
  редактирования гонки.

**Организационное:**

- организаторы заводят промокоды на странице гонки; коды раздаются вне системы.
- если промокод надо остановить — снять `is_active` (удалять использованный код нельзя).
- ошибочно выданную скидку исправлять вручную через админку платежей: изменение `Payment.status`
  сразу возвращает/забирает квоту кода.
