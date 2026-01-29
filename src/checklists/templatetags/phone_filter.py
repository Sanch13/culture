from django import template
from checklists.utils import format_phone_number  # Импорт нашей функции из utils

register = template.Library()


@register.filter(name="format_phone")
def format_phone_filter(value):
    """
    Использование в шаблоне: {{ user.phone|format_phone }}
    """
    return format_phone_number(value)
