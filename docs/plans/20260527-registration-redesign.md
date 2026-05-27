# Registration Page Redesign

## Overview

Переработать страницу регистрации (`/register/`) на новый визуальный дизайн из `scratch/Регистрация.html`.  
Создать независимый base-шаблон `base-2.html` и CSS-файл `theme-2.css`, не затрагивая существующий `base.html` и `theme.css`.

**Что меняется:**
- Новый UI: тёмный nav с логотипом, карточки с белым фоном, двухколоночная сетка (форма + сайдбар)
- Шрифт Rubik вместо старого Bootstrap-стека
- Vanilla JS вместо jQuery/Bootstrap для nav-dropdown и интерактивности формы
- Полный тёмный footer с тремя колонками (как в текущем base.html)

**Что не меняется:**
- `base.html`, `theme.css` — продолжают работать для всех остальных страниц
- Django-логика формы: `RegForm`, URL `register`, CSRF, поля `first_name/last_name/email/phone/password`
- Структура URL и вьюхи

## Context

- **Эталонный дизайн**: `scratch/Регистрация.html` (полный самодостаточный HTML со стилями и JS)
- **Текущий base**: `src/templates/website/base.html` (логотип `/static/images/logo.png`, nav-ссылки, footer с menu_tags)
- **Текущий register**: `src/templates/website/register.html` (extends base.html, форма reg_form)
- **Форма**: `src/website/forms.py` → `RegForm` поля: `first_name`, `last_name`, `email`, `phone`, `password`; виджеты с `class="form-control"` (Bootstrap)
- **Nav-ссылки**: Правила `/page/rules/`, Команды `{% url 'all_teams' 'kolco24_2025' %}`
- **Footer**: использует `{% load cache menu_tags %}` + `{% footer_menu %}` для колонки "Информация и документы"

## Development Approach

- **Тестирование**: Regular (code first — шаблоны не покрываются unit-тестами, проверка визуальная)
- Задачи выполнять последовательно
- Не трогать существующие файлы `base.html`, `theme.css`
- Перед коммитом: `make format && make lint`

## Solution Overview

1. **theme-2.css** — весь CSS из `scratch/Регистрация.html` плюс:
   - `.footer-dark` — тёмный footer с 3 колонками (адаптирован из base.html)
   - `.nav-user` + `.nav-user-menu` — dropdown для авторизованного пользователя (vanilla JS)
   - `.brand-logo` — стиль для `<img>` логотипа вместо CSS-кружка из scratch

2. **base-2.html** — Django-шаблон:
   - Загружает theme-2.css и Rubik через Google Fonts
   - Nav: реальный логотип + ссылки из base.html + dropdown пользователя (vanilla JS)
   - Блоки: `title`, `head_descr`, `og_*`, `extra_head`, `navbar`, `content`, `footer_menu`, `footer_js_include`
   - Footer: полный тёмный, три колонки, menu_tags

3. **register.html** — расширяет base-2.html:
   - Хлебные крошки, заголовок страницы
   - Форма: кастомный HTML с `class="input"` (не рендерить `{{ reg_form.field }}` — Bootstrap-класс конфликтует)
   - Обработка ошибок: `reg_form.field.errors` под каждым полем + `reg_form.non_field_errors`
   - Сайдбар: "Зачем регистрироваться" + "Нужна помощь?"
   - JS из scratch: показ/скрытие пароля, strength meter

## Technical Details

**Рендер полей формы** — вместо `{{ reg_form.field }}` писать вручную:
```html
<input class="input{% if reg_form.first_name.errors %} has-error{% endif %}"
       id="firstName" name="first_name" type="text"
       value="{{ reg_form.first_name.value|default:'' }}"
       placeholder="Алексей" autocomplete="given-name">
{% if reg_form.first_name.errors %}
  <span class="hint err">{{ reg_form.first_name.errors|join:", " }}</span>
{% endif %}
```

**Nav dropdown** — vanilla JS, закрытие по клику вне:
```js
document.addEventListener('click', function(e) {
  var m = document.getElementById('navUser');
  if (m && !m.contains(e.target)) m.classList.remove('is-open');
});
```

**Footer menu_tags** — тег `{% footer_menu %}` рендерит `<li>...</li>` строки, оборачивать в `<ul>`.

## Implementation Steps

### Task 1: Создать theme-2.css

**Files:**
- Create: `src/static/css/theme-2.css`

- [x] Скопировать все CSS-правила из `<style>` тега `scratch/Регистрация.html`
- [x] Заменить `.brand-mark` (CSS-кружок) на `.brand-logo` (стиль для `<img>`: `height: 32px; width: auto`)
- [x] Добавить `.nav-user`, `.nav-user-btn`, `.nav-user-menu`, `.nav-user.is-open` — dropdown для юзера
- [x] Добавить `.nav-user-menu a`, `.nav-user-menu button`, `.menu-divider`, `.menu-text` — элементы dropdown
- [x] Добавить `.footer-dark`, `.footer-dark .footer-grid`, `.footer-dark .footer-cols` — тёмный 3-колоночный footer
- [x] Добавить `.footer-dark h6`, `.footer-dark ul/li/a`, `.footer-dark .footer-brand`, `.footer-dark .footer-bottom`
- [x] Добавить responsive для `.footer-dark` (920px, 720px, 600px) + `@media print`
- [x] Добавить `.main-container { min-height: ... }` чтобы footer прижимался к низу

### Task 2: Создать base-2.html

**Files:**
- Create: `src/templates/website/base-2.html`

- [x] `<head>`: charset, viewport, title-block, description-block, Rubik от Google Fonts, theme-2.css, favicon, OG-мета-теги (как в base.html)
- [x] `{% block extra_head %}` в `<head>`
- [x] `{% block navbar %}`: `<header class="nav" id="siteNav">` с burger-кнопкой (inline onclick toggle)
- [x] В nav: `<img src="/static/images/logo.png" class="brand-logo">` + текст "Кольцо 24"
- [x] Nav-ссылки: Правила `/page/rules/`, Команды `{% url 'all_teams' 'kolco24_2025' %}`
- [x] Nav auth: `{% if user.is_active %}` → `.nav-user` dropdown с `{{ user.last_name }} {{ user.first_name }}`, staff-ссылки, superuser-ссылки, impersonate, logout-форма; `{% else %}` → ссылка "Войти" `{% url 'passlogin' %}`
- [x] Impersonator warning: `{% if request.session.impersonator_id %}` → предупреждение в nav
- [x] `<div class="main-container">{% block content %}{% endblock %}</div>`
- [x] `{% block footer_menu %}`: `<footer class="footer-dark d-print-none">` с 3 колонками
- [x] Footer col 1 "Навигация": те же ссылки что в base.html (Главная, Вход, VK-архивы)
- [x] Footer col 2 "Информация и документы": `{% load cache menu_tags %}{% cache 3600 "footer_menu" %}{% footer_menu %}{% endcache %}`
- [x] Footer col 3 "Сообщество и друзья": email, ТК Спутник, Школы (как в base.html)
- [x] Footer `.footer-bottom`: копирайт + email + VK-ссылка
- [x] `{% endblock %}` для footer_menu
- [x] Inline `<script>` для закрытия nav-user dropdown по клику вне него
- [x] `{% block footer_js_include %}{% endblock %}`

### Task 3: Обновить register.html

**Files:**
- Modify: `src/templates/website/register.html`

- [ ] Сменить `{% extends "website/base.html" %}` на `{% extends "website/base-2.html" %}`
- [ ] Переписать `{% block content %}` полностью
- [ ] Добавить `<div class="page"><div class="container">`
- [ ] Хлебные крошки: `<nav class="crumbs">` — Главная / Регистрация
- [ ] Заголовок: `<header class="page-head"><h1>Регистрация</h1><div class="sub">...</div>` + login-hint "Уже есть аккаунт? Войти →"
- [ ] `<div class="grid">` — левая колонка форма, правая сайдбар
- [ ] Форма `<form action="{% url 'register' %}" method="POST">{% csrf_token %}` — card с секциями
- [ ] Секция "Аккаунт": поля Имя/Фамилия (form-grid), Email/Телефон, Пароль (с strength meter и toggle)
- [ ] Каждое поле: кастомный `<input class="input">` с `name=` (first_name, last_name, email, phone, password), `value="{{ reg_form.X.value|default:'' }}"`, ошибки под полем
- [ ] `reg_form.non_field_errors` — alert-блок над submit-кнопкой
- [ ] Submit-row: кнопки "Отмена" (→ /) и "Зарегистрироваться"
- [ ] Сайдбар: card "Зачем регистрироваться" (два why-item: Создать команду, Связь во время гонки)
- [ ] Сайдбар: card "Нужна помощь?" с email и Telegram (из scratch)
- [ ] `{% block footer_js_include %}`: JS для toggle пароля + strength meter (из scratch)

### Task 4: Проверка

- [ ] Запустить `docker compose up -d kolco24_db && uv run python src/manage.py runserver 0:8080`
- [ ] Открыть `/register/` в браузере, проверить: отображение формы, логотип, nav-ссылки, footer
- [ ] Проверить мобильный вид (burger-меню, одна колонка)
- [ ] Проверить отправку формы с ошибками — ошибки должны показываться под полями
- [ ] Убедиться что остальные страницы (главная, /admin) не сломались
- [ ] `make format && make lint` — без ошибок
- [ ] `uv run pytest --reuse-db` — все тесты проходят

### Task 5: Финал

- [ ] Закоммитить: `git add src/static/css/theme-2.css src/templates/website/base-2.html src/templates/website/register.html`
- [ ] Переместить план: `mv docs/plans/20260527-registration-redesign.md docs/plans/completed/`

## Post-Completion

**Ручная проверка UX:**
- Форма с pre-filled значениями при ошибке валидации (проверить что `value=` правильно заполняется)
- Dropdown пользователя в nav — открытие/закрытие, staff-ссылки у staff-юзера
- Password strength meter на разных паролях
- Footer на узком экране (< 600px) — одна колонка
