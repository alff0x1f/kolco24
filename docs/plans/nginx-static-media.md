# План: раздача static и media через nginx

## Context

WhiteNoise — временное решение для раздачи статики. В production nginx эффективнее:
даёт sendfile, gzip, правильные Cache-Control заголовки и не нагружает gunicorn.

Ситуация до изменений:
- `/static/` — WhiteNoise (middleware), файлы запечены в образ при `docker build`
- `/media/` — **не раздавалось вообще**: нет URL-хендлера в `urls.py`, WhiteNoise медиа не трогает

После:
- Оба пути раздаёт nginx напрямую с volume
- Gunicorn получает только app-запросы

## Архитектура

```
External nginx (nginx_network)
    └─► kolco24_nginx  (nginx:alpine)
            ├── /static/* → volume: static (populated by collectstatic at deploy)
            ├── /media/*  → volume: media
            └── /*        → proxy → kolco24_django:8000 (kolco24_default)
```

## Как staticfiles попадают в nginx

Staticfiles запекаются в Docker-образ при билде (`RUN python manage.py collectstatic`).
Чтобы nginx имел к ним доступ, `kolco24_migrate` при деплое запускает `collectstatic`
повторно — уже с named volume `static` в качестве `STATIC_ROOT`. Nginx монтирует этот же volume.

## Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `deploy/nginx.conf` | создан: `/static/` и `/media/` — alias, всё остальное — proxy_pass |
| `docker-compose_v2.yml` | `kolco24_migrate` → добавлен `collectstatic`; добавлен `kolco24_nginx`; `kolco24_web` убран из `nginx_network`; добавлен volume `static` |
| `docker-compose.yml` | добавлен `kolco24_nginx` с bind-mount; `kolco24_web` убран из `nginx_network` |
| `src/kolco24/settings.py` | убран `WhiteNoiseMiddleware` и `STATICFILES_STORAGE` |

## Верификация

```bash
# Поднять стек
docker compose -f docker-compose_v2.yml up -d

# Проверить nginx
docker logs kolco24_nginx

# Статика (ожидать 200 + Cache-Control: immutable)
curl -I https://<host>/static/admin/css/base.css

# Медиа (ожидать 200 + Cache-Control: max-age=2592000)
curl -I https://<host>/media/<any-uploaded-file>

# Приложение всё ещё работает
curl -I https://<host>/
```

## Примечание для dev окружения

Перед запуском `docker compose up` нужно один раз выполнить `collectstatic` локально,
чтобы в `./src/staticfiles/` были файлы для nginx:

```bash
python src/manage.py collectstatic
```
