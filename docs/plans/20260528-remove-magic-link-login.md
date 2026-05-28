# Remove Magic Link Login (/login/)

## Overview

Удалить самодельный механизм входа по одноразовой ссылке (FastLogin / magic link), реализованный на `/login/`. Функционал больше не нужен. Удаляем модель, вьюхи, форму, email-функцию, URL-маршруты и шаблон. Таблица `website_fastlogin` удаляется из БД через миграцию.

## Context (from discovery)

- **Модель**: `FastLogin` — `src/website/models/models.py:566-582`
- **Вьюхи**: `login()` (строка 675) и `login_by_key()` (строка 688) — `src/website/views/views_.py`
- **Форма**: `FastLoginForm` — `src/website/forms.py:85-103`
- **Email**: `send_login_email()` — `src/website/email.py:6-23`
- **URLs**: маршруты `login/` и `login/<key>/` — `src/website/urls.py:46-47`
- **Шаблон**: `src/templates/website/login.html`
- **Экспорт**: `login`, `login_by_key` — `src/website/views/__init__.py:40-41`
- **НЕ трогаем**: `PassLoginView` / `/passlogin/`, стандартные Password Reset вьюхи, `@login_required`

## Development Approach

- **testing approach**: Regular (код → запуск тестов)
- Удалять компоненты в порядке от зависимых к базовым
- После каждой задачи убеждаться, что проект импортируется без ошибок
- Все тесты должны проходить перед переходом к следующей задаче

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix

## Implementation Steps

### Task 1: Удалить модель FastLogin и сгенерировать миграцию

`makemigrations` детектирует удаление модели только после того, как класс убран из кода — поэтому сначала удаляем, потом генерируем миграцию.

**Files:**
- Modify: `src/website/models/models.py`
- Create: `src/website/migrations/<next_number>_delete_fastlogin.py`

- [x] удалить класс `FastLogin` (строки 566-582) из `src/website/models/models.py`
- [x] удалить импорт `random`, если он используется только в FastLogin: `grep -n "random" src/website/models/models.py` (random используется в строке 407, импорт оставлен)
- [x] сгенерировать миграцию: `uv run python src/manage.py makemigrations website --name delete_fastlogin`
- [x] проверить содержимое миграции — должен быть `migrations.DeleteModel(name="FastLogin")`
- [x] применить миграцию: `uv run python src/manage.py migrate`
- [x] убедиться, что `grep -r FastLogin src/` не находит ничего в коде (кроме файлов миграций) (оставшиеся ссылки в views_.py и forms.py удалятся в Tasks 3 и 5)

### Task 3: Удалить вьюхи login() и login_by_key() из views_.py

**Files:**
- Modify: `src/website/views/views_.py`

- [x] удалить функцию `login()` (строки 675-685)
- [x] удалить функцию `login_by_key()` (строки 688-712)
- [x] удалить импорт `from website.email import send_login_email` (строка 37)
- [x] удалить `FastLoginForm` из строки импортов форм (строка 41)
- [x] удалить `FastLogin` из строки импортов моделей (строка 56)
- [x] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 4: Удалить экспорт из views/__init__.py и URL-маршруты

**Files:**
- Modify: `src/website/views/__init__.py`
- Modify: `src/website/urls.py`

- [x] удалить строки 40-41 (`login`, `login_by_key`) из `src/website/views/__init__.py`
- [x] удалить `path("login/", views.login, name="login")` из `src/website/urls.py` (строка 46)
- [x] удалить `re_path(r"^login/(?P<login_key>...)", views.login_by_key)` из `src/website/urls.py` (строка 47)
- [x] убедиться, что `re_path` импорт не остался висеть в urls.py без использования (используется для других маршрутов)
- [x] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 5: Удалить FastLoginForm, send_login_email и шаблон

**Files:**
- Modify: `src/website/forms.py`
- Modify: `src/website/email.py`
- Delete: `src/templates/website/login.html`

- [x] удалить класс `FastLoginForm` (строки 85-103) из `src/website/forms.py`
- [x] удалить функцию `send_login_email()` (строки 6-23) из `src/website/email.py`
- [x] удалить шаблон: `rm src/templates/website/login.html`
- [x] проверить, что `login.html` не рефернцируется нигде в коде: `grep -r "login.html" src/`
- [x] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 6: Финальная проверка

**Files:** —

- [ ] запустить `make format` (ruff --fix, black, isort)
- [ ] запустить `make lint` — ошибок быть не должно
- [ ] запустить полный тест-сюит: `uv run pytest`
- [ ] убедиться, что `grep -r "FastLogin\|login_by_key\|send_login_email\|FastLoginForm" src/` ничего не находит
- [ ] переместить план: `mv docs/plans/20260528-remove-magic-link-login.md docs/plans/completed/`

## Post-Completion

**Деплой**: при деплое на продакшн выполнить `manage.py migrate` — таблица `website_fastlogin` будет удалена из продакшн БД. Данные таблицы (временные ключи входа) не нужны и безопасно удаляются.

**Проверить**: убедиться, что переход на `/login/` возвращает 404 (маршрут удалён).
