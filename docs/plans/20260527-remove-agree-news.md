# Удалить поле agree_news («подписка на новости»)

## Overview

Из предыдущего плана (20260527-registration-agreements) в форму регистрации случайно попал чекбокс
«Получать новости о следующих гонках на e-mail — не чаще раза в месяц» (`agree_news`).
Поле не нужно, миграции ещё нигде не применялись — удаляем полностью.

## Context

- **Модель**: `src/website/models/models.py` — поле `agree_news = BooleanField(default=False)` в `Profile`
- **Миграция**: `src/website/migrations/0064_add_agree_news_to_profile.py` — добавляет поле, её зависимость `0063`
- **Следующая миграция**: `src/website/migrations/0065_unique_user_email.py` — зависит от `0064`, надо перевесить на `0063`
- **Форма**: `src/website/forms.py` — поле `agree_news` в `RegForm` + сохранение в `save()`
- **Вьюха**: `src/website/views/views_.py` — чтение и сохранение `agree_news` (строки 199, 211)
- **Шаблон**: `src/templates/website/register.html` — чекбокс `agree_news` (строки 141–148)
- **Тесты**: `src/website/tests.py` — функция `test_reg_form_agree_news_is_optional` (строки 275–282)

## Development Approach

- Regular (code first, then проверка)
- Миграции не применялись — `0064` удаляем, `0065` перевешиваем на `0063`
- Перед коммитом: `make format && make lint`

## Implementation Steps

### Task 1: Удалить миграцию и перевесить зависимость

**Files:**
- Delete: `src/website/migrations/0064_add_agree_news_to_profile.py`
- Modify: `src/website/migrations/0065_unique_user_email.py`

- [ ] Удалить файл `src/website/migrations/0064_add_agree_news_to_profile.py`
- [ ] В `0065_unique_user_email.py` заменить зависимость `("website", "0064_add_agree_news_to_profile")` на `("website", "0063_remove_coupons")`

### Task 2: Удалить поле из модели и формы

**Files:**
- Modify: `src/website/models/models.py`
- Modify: `src/website/forms.py`

- [ ] Удалить `agree_news = models.BooleanField(default=False)` из `Profile` в `models.py`
- [ ] Удалить `agree_news = forms.BooleanField(required=False)` из `RegForm` в `forms.py`
- [ ] Удалить строку `self.fields["agree_news"].required = ...` если есть (нет — пропустить)
- [ ] Удалить строку сохранения `user.profile.agree_news = self.cleaned_data.get("agree_news", False)` из `RegForm.save()`

### Task 3: Удалить из вьюхи и шаблона

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/templates/website/register.html`

- [ ] Удалить `agree_news = form.cleaned_data.get("agree_news", False)` (строка ~199) из вьюхи
- [ ] Удалить `user.profile.agree_news = agree_news` (строка ~211) из вьюхи
- [ ] Удалить блок `<div>…agree_news…</div>` (строки 141–148) из `register.html`

### Task 4: Удалить тест и проверить

**Files:**
- Modify: `src/website/tests.py`

- [ ] Удалить функцию `test_reg_form_agree_news_is_optional` (строки 275–282) из `tests.py`
- [ ] `make format && make lint` — без ошибок
- [ ] `uv run pytest --reuse-db` — все тесты проходят

### Task 5: Финал

- [ ] Переместить план: `mv docs/plans/20260527-remove-agree-news.md docs/plans/completed/`
