from email.message import EmailMessage
from datetime import datetime, timedelta

from django.utils import timezone
from django.conf import settings

from celery import shared_task

from checklists.utils import send_message
from checklists.services import generate_schedule, prepare_daily_notifications


@shared_task
def task_generate_weekly_schedule():
    """
    Задача для робота. Запускается по пятницам.
    Генерирует расписание на следующую неделю (или две).
    """
    today = timezone.now().date()

    # Вычисляем следующий понедельник
    # today.weekday(): 0=Пн ... 4=Пт ... 6=Вс
    # Если сегодня Пятница (4), то до понедельника (0) осталось 3 дня.
    days_until_monday = 7 - today.weekday()
    next_monday = today + timedelta(days=days_until_monday)

    # Генерируем на 14 дней вперед (с запасом)
    # Функция безопасна: если на понедельник уже есть запись, она её не перезапишет.
    result = generate_schedule(start_date=next_monday, days_count=14)

    return f"Auto-generation report: {result}"


@shared_task
def send_admin_email(text=None):
    # logger.info("---------Start---------send_admin_email()---------")
    try:
        text_body = (
            text
            or f"Culture APP send email to Admin {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        message = EmailMessage()
        message["Subject"] = "Culture APP"
        message["From"] = settings.EMAIL_HOST_USER
        message["To"] = settings.ADMIN_EMAIL
        message.set_content(text_body)

        send_message(message=message)

        # logger.info("Email успешно отправлен админу")
        # logger.info("---------End---------send_admin_email()---------")
    except Exception:
        # logger.exception(f"Ошибка: {e}")
        pass


@shared_task
def send_inspection_reminders():
    """Ежедневная отправка email с напоминанием о проверке (9 утра)"""
    # Получаем готовые данные
    emails_to_send = prepare_daily_notifications()

    for email_data in emails_to_send:
        # Тут вызываешь свою уже готовую функцию отправки
        send_email.delay(email_data)
        print(f"Отправка письма на {email_data['recipient_email']}...")  # Заглушка

    return f"Обработано {len(emails_to_send)} уведомлений."


@shared_task
def send_email(email_data):
    try:
        message = EmailMessage()
        message["Subject"] = email_data["subject"]
        message["From"] = settings.EMAIL_HOST_USER
        message["To"] = email_data["recipient_email"]
        message.set_content(email_data["body"])

        send_message(message=message)

        # logger.info("Email успешно отправлен админу")
        # logger.info("---------End---------send_admin_email()---------")
    except Exception:
        # logger.exception(f"Ошибка: {e}")
        pass
