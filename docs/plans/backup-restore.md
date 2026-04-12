# План: команды backup_db и restore_db

## Context

Нужны Django management commands для создания и восстановления бэкапов PostgreSQL.
Бэкапы хранятся локально в Docker volume. Регулярные бэкапы — через отдельный Docker-сервис с cron.

---

## Изменяемые файлы

| Файл | Что меняем |
|------|-----------|
| `Dockerfile` | Добавить `postgresql-client-15` в apt-get |
| `docker-compose_v2.yml` | Добавить сервис `kolco24_backup` и volume `backups` |
| `deploy/kolco24.env.example` | Добавить переменные `BACKUP_DIR`, `BACKUP_RETENTION_DAYS` |
| `src/website/management/commands/backup_db.py` | **Новый файл** |
| `src/website/management/commands/restore_db.py` | **Новый файл** |
| `Makefile` | Добавить таргеты `backup` и `restore` |

---

## Шаг 1 — Dockerfile

Добавить `postgresql-client-15` в `apt-get install` (совпадает с версией prod-БД `postgres:15`):

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates postgresql-client-15 \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
```

---

## Шаг 2 — backup_db management command

`src/website/management/commands/backup_db.py`

**Аргументы:**
- `--output-dir` — переопределить директорию (по умолчанию `BACKUP_DIR` env или `/app/backups`)
- нет флагов — просто запускает дамп

**Логика:**
1. Прочитать DB-настройки из `django.conf.settings.DATABASES["default"]`
2. Создать директорию если не существует
3. Сформировать имя файла: `kolco24_YYYY-MM-DD_HHMMSS.dump`
4. Запустить `pg_dump -Fc -h HOST -p PORT -U USER -d DBNAME -f /path/file.dump`
   - Пароль передавать через `env["PGPASSWORD"]` в `subprocess.run`, не через аргументы CLI
5. Проверить returncode, вывести размер файла
6. Удалить файлы старше `BACKUP_RETENTION_DAYS` (default: 30) из директории
7. Вывести итог в `self.stdout`

---

## Шаг 3 — restore_db management command

`src/website/management/commands/restore_db.py`

**Аргументы:**
- `backup_file` — позиционный, путь к файлу (необязательный)
- `--latest` — автоматически найти последний файл в `BACKUP_DIR`
- `--no-confirm` — пропустить интерактивное подтверждение (для скриптов)

**Логика:**
1. Разрешить файл: явный путь > `--latest` (найти последний `*.dump` в `BACKUP_DIR`)
2. Проверить что файл существует
3. Запросить подтверждение (если не `--no-confirm` и `sys.stdin.isatty()`)
4. Запустить `pg_restore --clean --if-exists --no-owner -Fc -h HOST -p PORT -U USER -d DBNAME /path/file.dump`
   - `--clean --if-exists` — очищает объекты внутри БД без DROP DATABASE (работает пока приложение запущено)
5. Проверить returncode, вывести результат

---

## Шаг 4 — docker-compose_v2.yml

**Новый сервис `kolco24_backup`** — запускает `backup_db` раз в сутки через shell-loop:

```yaml
kolco24_backup:
  image: ${KOLCO24_IMAGE:?KOLCO24_IMAGE is required}
  container_name: kolco24_backup
  restart: unless-stopped
  command:
    - /bin/sh
    - -c
    - |
      while true; do
        python manage.py backup_db
        sleep 86400
      done
  env_file:
    - ./deploy/kolco24.env
  volumes:
    - backups:/app/backups
  depends_on:
    kolco24_migrate:
      condition: service_completed_successfully
  networks:
    - kolco24_default
```

**Новый volume** в секции `volumes:`:
```yaml
backups:
```

**Также смонтировать `backups` в `kolco24_web`** — чтобы можно было запустить `restore_db` через `docker exec`:
```yaml
volumes:
  - media:/app/media
  - backups:/app/backups
```

---

## Шаг 5 — deploy/kolco24.env.example

Добавить секцию:

```
# Backup
BACKUP_DIR=/app/backups
BACKUP_RETENTION_DAYS=30
```

---

## Шаг 6 — Makefile

```makefile
backup:
	docker compose -f docker-compose_v2.yml exec kolco24_django python manage.py backup_db

restore:
	docker compose -f docker-compose_v2.yml exec -it kolco24_django python manage.py restore_db --latest
```

> Имя контейнера `kolco24_django` — из `container_name` в docker-compose_v2.yml.

---

## Использование

```bash
# Ручной бэкап
python manage.py backup_db
python manage.py backup_db --output-dir /tmp/backups

# Восстановление
python manage.py restore_db --latest               # последний бэкап (с подтверждением)
python manage.py restore_db path/to/file.dump      # конкретный файл
python manage.py restore_db --latest --no-confirm  # без вопросов (в скриптах)

# Через make (production)
make backup
make restore
```

---

## Проверка

1. `make build-push` — убедиться что `pg_dump` доступен в образе: `docker run ... pg_dump --version`
2. Запустить `python manage.py backup_db` — файл появляется в `BACKUP_DIR`, размер > 0
3. Запустить `python manage.py restore_db --latest` — восстановление проходит без ошибок
4. Запустить `docker compose up` — убедиться что `kolco24_backup` стартует и делает первый дамп
5. Проверить retention: создать старые файлы вручную, запустить `backup_db` повторно, старые файлы удалены
