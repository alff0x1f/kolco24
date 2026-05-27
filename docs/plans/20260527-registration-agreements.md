# Registration: добавить раздел «Согласия»

## Overview

Добавить секцию «Согласия» на форму регистрации (`/register/`), которая была в дизайн-макете `scratch/Регистрация.html`, но не вошла в предыдущий план.

**Три чекбокса:**
- `agree_terms` — согласие с правилами и пользовательским соглашением (**обязательный**)
- `agree_privacy` — согласие на обработку ПДн (**обязательный**)
- `agree_news` — подписка на новости (необязательный)

Валидация — серверная (Django `BooleanField`) + клиентский `novalidate`-режим (ошибки показываются под чекбоксами при возврате формы).

## Context

- **Дизайн**: `scratch/Регистрация.html`, строки 592–620 (секция `<!-- Agreements -->`)
- **CSS**: `.check` стили в `scratch/Регистрация.html`, строки 304–341 — в `theme-2.css` **отсутствуют**
- **Форма**: `src/website/forms.py` → `RegForm` — нет полей согласий
- **Шаблон**: `src/templates/website/register.html` — раздел «Согласия» отсутствует между секцией «Аккаунт» и `submit-row`
- **URLs**: `{% url 'rules' %}` (правила), `{% url 'privacy_policy' %}` (политика ПДн)

## Development Approach

- **Тестирование**: Regular (code first)
- Задачи выполнять последовательно
- Перед коммитом: `make format && make lint`

## Solution Overview

1. Добавить `.check` CSS в `theme-2.css`
2. Добавить поля `agree_terms`, `agree_privacy`, `agree_news` в `RegForm`
3. Добавить HTML-секцию «Согласия» в `register.html` с ошибками Django

## Implementation Steps

### Task 1: Добавить .check CSS в theme-2.css

**Files:**
- Modify: `src/static/css/theme-2.css`

- [x] Добавить стили `.check`, `.check input`, `.check .box`, `.check:hover .box`, `.check input:checked + .box`, `.check input:checked + .box::after`, `.check input:focus-visible + .box` из `scratch/Регистрация.html` (строки 304–341)
- [x] Убедиться, что стили добавлены в правильном месте (рядом с секцией формы)

### Task 2: Добавить поля согласий в RegForm

**Files:**
- Modify: `src/website/forms.py`

- [x] Добавить `agree_terms = forms.BooleanField(required=True)` в `RegForm`
- [x] Добавить `agree_privacy = forms.BooleanField(required=True)` в `RegForm`
- [x] Добавить `agree_news = forms.BooleanField(required=False)` в `RegForm`
- [x] Написать тесты для `RegForm`: submit с незаполненными `agree_terms`/`agree_privacy` возвращает ошибки
- [x] Написать тест: submit со всеми заполненными полями проходит валидацию
- [x] Запустить `uv run pytest --reuse-db` — все тесты проходят

### Task 3: Добавить секцию «Согласия» в register.html

**Files:**
- Modify: `src/templates/website/register.html`

- [x] Вставить `<div class="card-section">` с заголовком «Согласия» между секцией «Аккаунт» и `submit-row`
- [x] Добавить три чекбокса с классом `.check` (custom styling)
- [x] `agree_terms`: `<input type="checkbox" name="agree_terms">` + текст со ссылками на `{% url 'rules' %}` и `{% url 'privacy_policy' %}` + вывод ошибок `{{ reg_form.agree_terms.errors|join:", " }}`
- [x] `agree_privacy`: `<input type="checkbox" name="agree_privacy">` + текст со ссылкой `{% url 'privacy_policy' %}` + вывод ошибок `{{ reg_form.agree_privacy.errors|join:", " }}`
- [x] `agree_news`: `<input type="checkbox" name="agree_news">` + текст (без ошибок — не обязательный)

### Task 4: Проверка

- [x] `make format && make lint` — без ошибок
- [x] `uv run pytest --reuse-db` — все тесты проходят
- [x] Визуально: открыть `/register/`, убедиться что раздел «Согласия» отображается между полями и кнопкой (manual test - skipped, not automatable)

### Task 5: Финал

- [ ] Переместить план: `mv docs/plans/20260527-registration-agreements.md docs/plans/completed/`

## Post-Completion

**Ручная проверка:**
- Отправить форму без чекбоксов agree_terms/agree_privacy — должны появиться ошибки под ними
- Отправить форму со всеми заполненными полями — регистрация проходит успешно
- Проверить на мобильном (< 600px) — чекбоксы не обрезаются
