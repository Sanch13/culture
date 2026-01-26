import ssl
import smtplib
from email.message import Message

from django.conf import settings


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
