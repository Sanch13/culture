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
    calculate_daily_location_scores,
    prepare_weekly_notifications,
    prepare_monday_reminders,
    prepare_overdue_notifications,
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

        calculate_daily_location_scores(inspection.date_check)

        return f"Отчет {inspection_id} рассчитан. Итог: {score} баллов. Сводка участков обновлена."

    except Inspection.DoesNotExist:
        return f"Ошибка: Отчет {inspection_id} не найден."
    except Exception as e:
        # Логируем любую непредвиденную ошибку (например, деление на ноль)
        return f"КРИТИЧЕСКАЯ ОШИБКА при расчете отчета {inspection_id}: {str(e)}"


@shared_task
def send_weekly_schedule_digest():
    """
    Задача: Рассылка плана на следующую неделю.
    Запускается по пятницам в 14:30.
    """
    emails_to_send = prepare_weekly_notifications()

    if not emails_to_send:
        return "Нет заданий на следующую неделю. Рассылка не выполнена."

    success_count = 0
    for data in emails_to_send:
        try:
            send_email.delay(
                to=data["recipient_email"], subject=data["subject"], body=data["body"]
            )
            success_count += 1
            print(f"📧 [WEEKLY DIGEST] Sent to {data['recipient_email']}")
        except Exception as e:
            print(f"❌ Error sending to {data['recipient_email']}: {e}")

    return f"Успешно отправлено {success_count} еженедельных дайджестов."


@shared_task
def send_monday_reminders():
    """
    Задача: Рассылка напоминаний в понедельник утром.
    """
    emails_to_send = prepare_monday_reminders()

    if not emails_to_send:
        return "Нет заданий на эту неделю (со Вт по Вс). Рассылка не выполнена."

    success_count = 0
    for data in emails_to_send:
        try:
            send_email.delay(
                to=data["recipient_email"], subject=data["subject"], body=data["body"]
            )
            success_count += 1
        except Exception as e:
            print(f"❌ Error queuing email for {data['recipient_email']}: {e}")

    return f"Успешно поставлено в очередь {success_count} напоминаний на понедельник."


@shared_task
def send_admin_overdue_report():
    """
    Задача: Рассылка списка должников администраторам (в 14:00).
    """
    report_body = prepare_overdue_notifications()

    if not report_body:
        return "Должников нет или сегодня выходной. Письмо не отправлено."

    # Находим всех активных администраторов
    admins = User.objects.filter(role=User.ROLE_ADMIN, is_active=True)

    admin_emails = [admin.email for admin in admins if admin.email]

    if not admin_emails:
        return "Ошибка: В системе нет активных администраторов с email."

    # --- Подмена для теста (ПОТОМ УДАЛИТЬ) ---
    admin_emails = ["a.zubchyk@miran-bel.com"]
    # ----------------------------------------

    success_count = 0
    subject = (
        f"🚨 ВНИМАНИЕ: Незавершенные проверки на {timezone.now().strftime('%d.%m.%Y')}"
    )

    # Отправим каждому отдельно, чтобы у всех в ящике было красиво:
    for email in admin_emails:
        try:
            send_email(to=email, subject=subject, body=report_body)
            success_count += 1
            print(f"📧 [ADMIN OVERDUE] Sent to {email}")
        except Exception as e:
            print(f"❌ Error sending to {email}: {e}")

    return f"Успешно отправлено {success_count} отчетов администраторам."
