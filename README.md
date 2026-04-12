# kolco24

## Разработка

```bash
git clone git@github.com:alff0x1f/kolco24.git
cd kolco24
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
