# Миграция на uv и Python 3.12

## Контекст

Проект использовал Python 3.9 и `pip` с отдельными `requirements.txt` / `requirements-linters.txt`. Цель — перейти на
Python 3.12 и uv (по образцу `tk-sputnik.org`).

## Изменённые файлы

- `mise.toml` — `python = "3.9"` → `"3.12"`
- `pyproject.toml` — добавлены `[project]` с зависимостями и `[dependency-groups].dev` с линтерами и тестами
- `Dockerfile` — `pip install -r requirements.txt` заменён на `uv sync --frozen --no-dev`
- `Makefile` — команда `format` переключена на `uv run`
- `requirements.txt` — удалён
- `requirements-linters.txt` — удалён
- `uv.lock` — создан (61 пакет, Python 3.12.13)

## Структура зависимостей

Основные зависимости — в `[project].dependencies`, dev-зависимости (pytest, black, flake8, isort, ruff) — в
`[dependency-groups].dev`.

## Команды после миграции

```bash
uv sync --dev          # установить всё (включая dev)
uv sync --no-dev       # только prod-зависимости
uv add <package>       # добавить зависимость
uv run pytest          # запустить тесты
make format            # форматирование (ruff + black + isort через uv run)
```
