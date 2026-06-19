from urllib.parse import parse_qs, urlsplit

from django import template
from django.urls import Resolver404, resolve

register = template.Library()

# Auth pages that link into one another via ?next= (login → register → start → …).
# None of them is ever a valid final redirect target, so a ?next= pointing at one
# is either drilled through to its buried destination or dropped — otherwise a
# crawler stacks ever-growing percent-encoded URLs (a spider trap).
_AUTH_URL_NAMES = {
    "login",
    "register",
    "account_start",
    "account_verify",
    "magic_link",
}


@register.filter(name="safe_next")
def safe_next(candidate):
    """Reduce a ``?next=`` candidate to a real, local, non-auth destination path.

    Follows nested ``?next=`` chains buried inside auth pages so the login/register/
    start links can't accumulate, and returns ``""`` when there is no real
    destination behind the auth page. The result is always a bare same-site path
    (query stripped), safe to feed straight back into ``?next=``. Loop-bounded.
    """
    seen = 0
    while candidate and seen < 6:
        seen += 1
        parts = urlsplit(candidate)
        path = parts.path
        # Same-site absolute paths only; scheme/host or relative values are dropped.
        if parts.scheme or parts.netloc or not path.startswith("/"):
            return ""
        try:
            match = resolve(path)
        except Resolver404:
            return path
        if match.url_name not in _AUTH_URL_NAMES:
            return path
        # Auth page: drill into its own buried next, discard the rest.
        candidate = parse_qs(parts.query).get("next", [""])[0]
    return ""


@register.filter(name="dict_key")
def dict_key(d, key):
    return d.get(key, None)


@register.filter(name="ru_plural")
def ru_plural(value, forms):
    """Pick the Russian noun form that agrees with ``value``.

    ``forms`` is a comma-separated triple "one,few,many", e.g.
    ``"команда,команды,команд"`` → ``1 команда``, ``2 команды``, ``5 команд``.
    Returns only the noun (not the number) so the count can stay styled
    separately in the template. Standard rule:

    * ``one``  — n % 10 == 1 and n % 100 != 11 (1, 21, 31 …)
    * ``few``  — n % 10 in 2..4 and n % 100 not in 12..14 (2–4, 22–24 …)
    * ``many`` — everything else (0, 5–20, 11–14 …)
    """
    try:
        n = abs(int(float(value)))
    except (TypeError, ValueError):
        n = 0
    parts = forms.split(",")
    if len(parts) != 3:
        return forms
    one, few, many = parts
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many
