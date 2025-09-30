from django import template

from website.models import MenuItem

register = template.Library()


@register.inclusion_tag("website/_footer_menu.html")
def footer_menu():
    return {"menu": MenuItem.objects.all()}
