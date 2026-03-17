from django.core.validators import RegexValidator

cyrillic_regex = RegexValidator(
    regex=r"^[А-Яа-яЁё\s\-]+$",
    message="Используйте только русские буквы, пробел или дефис.",
)
