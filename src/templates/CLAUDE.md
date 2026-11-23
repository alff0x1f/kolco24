# templates

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

**Custom error pages**: `src/templates/{404,403,500}.html` (themed «Сбились с маршрута»), sharing
`src/static/css/error.css` (scoped under `.error-page`). Django's default handlers auto-load them by name — no
`handler404/500/403` wiring in `config/urls.py`. `404.html`/`403.html` extend `base-2.html` (rendered with a
`RequestContext`). `500.html` is **standalone by design** — a full `<!doctype html>` doc with hardcoded asset URLs, no
`{% static %}`/`{% url %}`/`{% footer_menu %}`/`{{ user }}`, so it renders with an empty context and a down DB.
