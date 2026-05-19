# kolco24

## Разработка

```bash
git clone git@github.com:alff0x1f/kolco24.git
cd kolco24
uv sync --dev
```

Запустить базу данных:

```bash
docker compose -f docker-compose-dbs.yml up -d
```

Применить миграции и запустить devserver:

```bash
python src/manage.py migrate
python src/manage.py runserver 0:8080
```

## Проверка отправки писем

В разработке (письмо уходит через SMTP напрямую):

```bash
python src/manage.py check_email your@email.com
```

В продакшене (письмо ставится в очередь django-mailer, затем отправляется контейнером `kolco24_runmailer`):

```bash
docker compose exec kolco24_django uv run python manage.py check_email your@email.com
```

Команда выводит статус и показывает, сколько писем стоит в очереди. Если письмо встало в очередь, но не доходит — проверьте логи `kolco24_runmailer`:

```bash
docker compose logs kolco24_runmailer
```

## Продакшен

### Сборка и публикация образа

```bash
make login          # авторизация в registry
make build-push     # сборка и push latest
make build-push TAG=v1.2.3  # с конкретным тегом
```

### Деплой на сервере

```bash
cp deploy/kolco24.env.example deploy/kolco24.env
# заполнить deploy/kolco24.env

echo "KOLCO24_IMAGE=registry.lab.tk-sputnik.org/kolco24:latest" > .env
docker compose -f docker-compose_v2.yml up -d
```

Для обновления до новой версии:

```bash
echo "KOLCO24_IMAGE=registry.lab.tk-sputnik.org/kolco24:v1.2.3" > .env
docker compose -f docker-compose_v2.yml pull
docker compose -f docker-compose_v2.yml up -d
```

> Требует внешней сети `nginx_network` (создаётся отдельно: `docker network create nginx_network`).
