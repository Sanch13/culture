from email.message import EmailMessage
from datetime import datetime, timedelta

from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model

from celery import shared_task

from checklists.models import Inspection
from checklists.utils import send_message
from checklists.services import (
    generate_schedule,
    prepare_daily_notifications,
    get_swap_notification_data,
    calculate_inspection_score,
)

User = get_user_model()


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
        send_email.delay(
            to=email_data["recipient_email"],
            subject=email_data["subject"],
            body=email_data["body"],
        )
        print(f"Отправка письма на {email_data['recipient_email']}...")  # Заглушка

    return f"Обработано {len(emails_to_send)} уведомлений."


@shared_task
def send_email(to: str, subject: str, body: str):
    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.EMAIL_HOST_USER
        message["To"] = to
        message.set_content(body)

        send_message(message=message)

        # logger.info("Email успешно отправлен админу")
        # logger.info("---------End---------send_admin_email()---------")
        return f"Письмо успешно отправлено на {to}"

    except Exception:
        # logger.exception(f"Ошибка: {e}")
        pass


@shared_task
def notify_user_about_swap(date_str, user_id):
    """
    Задача: Уведомить сотрудника, что на него перекинули смену.
    """
    data = get_swap_notification_data(date_str, user_id)

    if data:
        # Отправляем письмо
        send_email.delay(to=data["email"], subject=data["subject"], body=data["body"])
        print(f"📧 [SWAP NOTIFICATION] Sent to {data['email']}")
        return f"Sent swap email to {data['email']}"

    return "No email sent (invalid data or no email)"


@shared_task
def notify_admin_about_swap(message):
    """
    Задача: Уведомить админа, что произошел обмен сменами.
    """

    admins = User.objects.filter(role=User.ROLE_ADMIN)
    for admin in admins:
        if admin.email:
            send_email.delay(to=admin.email, subject="Автозамена", body=message)

    return f"Sent swap email to {admins}"


@shared_task
def task_calculate_score(inspection_id):
    """
    Фоновая задача для расчета баллов за отчет.
    Запускается после успешной сдачи отчета сотрудником.
    """
    try:
        # Подтягиваем отчет вместе с ответами, чтобы не делать лишних запросов внутри калькулятора
        inspection = Inspection.objects.prefetch_related("items").get(id=inspection_id)

        # Запускаем движок (передаем объект, а не ID)
        score = calculate_inspection_score(inspection)

        return f"Отчет {inspection_id} рассчитан. Итог: {score} баллов."

    except Inspection.DoesNotExist:
        return f"Ошибка: Отчет {inspection_id} не найден."
    except Exception as e:
        # Логируем любую непредвиденную ошибку (например, деление на ноль)
        return f"КРИТИЧЕСКАЯ ОШИБКА при расчете отчета {inspection_id}: {str(e)}"
