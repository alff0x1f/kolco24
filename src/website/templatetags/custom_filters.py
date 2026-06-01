from django import template

register = template.Library()


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
