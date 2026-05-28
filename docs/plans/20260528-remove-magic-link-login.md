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

### Task 1: Создать миграцию для удаления таблицы FastLogin

**Files:**
- Create: `src/website/migrations/<next_number>_delete_fastlogin.py`

- [ ] определить номер следующей миграции (`ls src/website/migrations/ | tail -5`)
- [ ] создать миграцию: `uv run python src/manage.py makemigrations website --name delete_fastlogin`
- [ ] проверить содержимое миграции — должен быть `migrations.DeleteModel(name="FastLogin")`
- [ ] применить миграцию локально: `uv run python src/manage.py migrate`
- [ ] убедиться, что таблица `website_fastlogin` исчезла из БД

### Task 2: Удалить модель FastLogin из models.py

**Files:**
- Modify: `src/website/models/models.py`

- [ ] удалить класс `FastLogin` (строки 566-582) из `src/website/models/models.py`
- [ ] удалить импорт `random` если он используется только в FastLogin (проверить grep)
- [ ] убедиться, что `from website.models import FastLogin` нигде не остался (`grep -r FastLogin src/`)
- [ ] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 3: Удалить вьюхи login() и login_by_key() из views_.py

**Files:**
- Modify: `src/website/views/views_.py`

- [ ] удалить функцию `login()` (строки 675-685)
- [ ] удалить функцию `login_by_key()` (строки 688-712)
- [ ] удалить импорт `from website.email import send_login_email` (строка 37)
- [ ] удалить `FastLoginForm` из строки импортов форм (строка 41)
- [ ] удалить `FastLogin` из строки импортов моделей (строка 56)
- [ ] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 4: Удалить экспорт из views/__init__.py и URL-маршруты

**Files:**
- Modify: `src/website/views/__init__.py`
- Modify: `src/website/urls.py`

- [ ] удалить строки 40-41 (`login`, `login_by_key`) из `src/website/views/__init__.py`
- [ ] удалить `path("login/", views.login, name="login")` из `src/website/urls.py` (строка 46)
- [ ] удалить `re_path(r"^login/(?P<login_key>...)", views.login_by_key)` из `src/website/urls.py` (строка 47)
- [ ] убедиться, что `re_path` импорт не остался висеть в urls.py без использования
- [ ] запустить `uv run python src/manage.py check` — ошибок быть не должно

### Task 5: Удалить FastLoginForm, send_login_email и шаблон

**Files:**
- Modify: `src/website/forms.py`
- Modify: `src/website/email.py`
- Delete: `src/templates/website/login.html`

- [ ] удалить класс `FastLoginForm` (строки 85-103) из `src/website/forms.py`
- [ ] удалить функцию `send_login_email()` (строки 6-23) из `src/website/email.py`
- [ ] удалить шаблон: `rm src/templates/website/login.html`
- [ ] проверить, что `login.html` не рефернцируется нигде в коде: `grep -r "login.html" src/`
- [ ] запустить `uv run python src/manage.py check` — ошибок быть не должно

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
