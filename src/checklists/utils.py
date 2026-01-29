import ssl
import smtplib
from email.message import Message

from django.conf import settings

import phonenumbers


def is_privileged_user(user):
    """
    Возвращает True, если пользователю разрешен доступ в админ-панель.
    Это Админы, Мастера или Staff.
    """
    return user.is_staff or user.role in ["admin", "master"]


def send_message(message: Message):
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT) as server:
        server.starttls(context=context)
        server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
        server.send_message(message)

    # logger.info(f"Письмо успешно отправлено!")


def format_phone_number(raw_phone):
    """
    Принимает строку (например, '375291234567')
    Возвращает красиво (например, '+375 29 123-45-67')
    Если ошибка - возвращает исходную строку.
    """
    if not raw_phone:
        return ""

    # Приводим к строке на всякий случай
    s_phone = str(raw_phone)

    # Если в начале нет плюса, добавляем его.
    # Библиотека лучше работает, когда номер начинается с +
    if not s_phone.startswith("+"):
        s_phone = "+" + s_phone

    try:
        # Парсим номер
        parsed_num = phonenumbers.parse(s_phone, None)

        # Проверяем валидность (опционально, но полезно)
        if not phonenumbers.is_valid_number(parsed_num):
            return raw_phone

        # Форматируем в международный формат
        return phonenumbers.format_number(
            parsed_num, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except phonenumbers.NumberParseException:
        # Если пришел мусор, возвращаем как есть, чтобы ничего не упало
        return raw_phone
