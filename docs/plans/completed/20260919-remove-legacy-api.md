# Удаление старых эндпоинтов `/api/*`

Статус: выполнено (2026-11, PR #312, #314; см. `docs/plans/20261113-offseason-cleanup-roadmap.md`, Task 2–4)
Дата: 2026-09-19

## Зачем

Старое Android-приложение `kolco24_app` ходит в `/api/*`. Новые клиенты —
`kolco24_app_v2` (Android) и `kolco24_ios` — целиком работают через `/app/*`
(подпись per-build HMAC + bearer-токен на админских записях).

`/api/*` **не имеет авторизации вообще**, кроме `contributors/`
(Bearer-токен). Все остальные view в `src/api/views/` не объявляют
`permission_classes` — то есть любой может читать составы команд с
персональными данными и писать старты/финиши.

Единственная частичная защита — `CheckpointTagCreateView`, где стоит
`if not self.request.user.is_superuser: return 401` — но и это проверка
сессии, а не токена, и обвешана TODO.

## Инвентарь эндпоинтов

Подключение: `src/website/urls.py:196` → `include("api.urls")`.

| эндпоинт | кто зовёт | замена в новых клиентах |
|---|---|---|
| `GET/POST /api/member_tag/` | старое app (`AddTagActivity`) | только чтение: `GET /app/race/<id>/member_tags/`. **Записи нет** |
| `POST /api/member_tag/touch/` | старое app (`AddTagActivity`) | нет |
| `GET /api/race/<id>/checkpoint/` | старое app (`DataDownloader`) | `GET /app/race/<id>/legend/` |
| `POST /api/race/<id>/checkpoint_tag/` | старое app | `POST /app/race/<id>/tags/` (с токеном) |
| `GET /api/race/<id>/teams/` | старое app (`DataDownloader`) | `GET /app/race/<id>/teams/` |
| ~~`POST /api/race/<id>/team_start/`~~ — **удалён 2026-10-08** | старое app, судейский режим | `POST /app/race/<id>/judge_scans/` |
| ~~`POST /api/race/<id>/team_finish/`~~ — **удалён 2026-10-08** | старое app, судейский режим | `POST /app/race/<id>/judge_scans/` |
| `POST /api/race/<id>/upload_photo/` (лежит в `website/urls.py:197`, не в `api.urls`) | старое app (`PhotoFragment`, `PhotoUploader`) | `POST /app/race/<id>/mark/<mark_id>/photo/<frame_id>` |
| `GET /api/race/<id>/teams.csv` | организаторы / скрипты, не приложение | нет (и BOM-less — читается скриптами) |
| `GET /api/contributors/` | внешний сайт, Bearer-токен | — (оставить) |
| `GET /api/ping/` | неизвестно | `/app/time/`? |

Старое приложение также содержит хардкод `https://kolco24.ru/api/v1/teams` —
такого маршрута на сервере нет (мёртвая ссылка).

## Сделано

- 2026-10-08: удалены `team_start/` и `team_finish/` (`TeamStartView`,
  `TeamFinishView`, их сериализаторы). Они перезаписывали
  `Team.start_time`/`finish_time`, которые идут в протокол. Теперь эти поля
  заполняют только отметки на КП старта/финиша (`/app/.../marks/`) и ручная
  правка в `/admin/`. Модели `TeamStartLog`/`TeamFinishLog`/`TeamMemberRaceLog`
  оставлены ради истории (их читают админка и `TeamMemberRaceLogView`).

## Открытые вопросы

1. **Провижининг браслетов.** `POST /api/member_tag/` (создание `Tag`:
   `number` + `nfc_uid`) и `POST /api/member_tag/touch/` в новых клиентах не
   реализованы — есть только чтение пула. Без замены этот кусок нельзя просто
   удалить. Варианты: перенести в `/app/` на per-person write layer, сделать
   веб-страницу в `apps.race`, или оставить как есть под авторизацией.
2. **`teams.csv`** — кто именно его дёргает и чем заменить (на сайте есть
   экспорт платежей, но не составов).
3. **`ping/`** — используется ли мониторингом.
4. **Стратегия отключения**: сразу удалить, или сначала закрыть авторизацией /
   вернуть 410 на переходный период, пока в поле не обновятся все телефоны.
5. **Данные**: `TeamStartLog` / `TeamFinishLog` / `TeamMemberRaceLog` пишутся
   только старым API. Модели оставляем ради истории или мигрируем в `JudgeScan`?

## Дальше

Продолжить брейншторм: ответить на вопросы 1–5, выбрать стратегию отключения,
затем оформить пошаговый план.
