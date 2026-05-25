import time
from email.message import EmailMessage
from datetime import datetime, timedelta

from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model

import structlog
from celery import shared_task

from checklists.models import Inspection
from checklists.utils import send_message
from checklists.services.services import (
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

logger = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=300,
    retry_backoff_max=3600,
    max_retries=5,
    retry_jitter=True,
)
def task_generate_weekly_schedule(self):
    """
    Задача для робота. Запускается по пятницам.
    Генерирует расписание на следующую неделю (или две).
    """
    start_perf = time.perf_counter()
    log = logger.bind(
        task_name=self.name, task_id=self.request.id, attempt=self.request.retries + 1
    )
    log.info("robot_generation_started - Робот начал плановую генерацию")
    try:
        today = timezone.now().date()

        # Вычисляем следующий понедельник
        # today.weekday(): 0=Пн ... 4=Пт ... 6=Вс
        # Если сегодня Пятница (4), то до понедельника (0) осталось 3 дня.
        days_until_monday = 7 - today.weekday()
        next_monday = today + timedelta(days=days_until_monday)

        log.info(
            "robot_calculating_dates - Робот рассчитывает за период",
            current_date=str(today),
            target_monday=str(next_monday),
        )

        # Генерируем на 21 дней вперед (с запасом)
        # Функция безопасна: если на понедельник уже есть запись, она её не перезапишет.
        result = generate_schedule(start_date=next_monday, days_count=21)

        duration = time.perf_counter() - start_perf
        log.info(
            "robot_generation_success - Робот успешно сгенерировал расписание и сохранил в БД",
            report=result,
            duration=round(duration, 4),
        )

        return f"Auto-generation report: {result}"

    except Exception as e:
        # 5. Если робот упал — это критическая ошибка (Error)
        log.error(
            "robot_generation_failed - Ошибка генерации расписания роботом",
            error=str(e),
            exc_info=True,  # Это прикрепит Traceback ошибки в JSON
        )
        # Пробрасываем ошибку дальше, чтобы Celery пометил задачу как Failed
        raise


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


@shared_task(bind=True, max_retries=3, retry_backoff=300, autoretry_for=(Exception,))
def send_inspection_reminders(self):
    """Ежедневная отправка email с напоминанием о проверке (9 утра)"""
    current_attempt = self.request.retries + 1
    start_perf = time.perf_counter()

    log = logger.bind(
        task_name=self.name, task_id=self.request.id, attempt=current_attempt
    )

    log.info("reminder_task_started - Запуск ежедневных напоминаний")

    try:
        # 1. Замеряем время подготовки данных
        prep_start = time.perf_counter()
        emails_to_send = prepare_daily_notifications()
        prep_duration = time.perf_counter() - prep_start

        log.info(
            "reminder_data_prepared - Данные подготовлены для отправки",
            count=len(emails_to_send),
            prep_duration=round(prep_duration, 4),
        )

        # 2. Рассылаем задачи
        for email_data in emails_to_send:
            send_email.delay(
                to=email_data["recipient_email"],
                subject=email_data["subject"],
                body=email_data["body"],
            )
            # Логируем только факт постановки в очередь (без тела письма, чтобы не забивать Loki)
            log.info(
                "email_dispatched - e-mail обработан",
                recipient=email_data["recipient_email"],
            )

        duration = time.perf_counter() - start_perf
        log.info(
            "reminder_task_finished - Рассылка напоминаний закончена",
            total_count=len(emails_to_send),
            duration=round(duration, 4),
        )

        return f"Processed {len(emails_to_send)} notifications."

    except Exception as exc:
        # Проверяем, последняя ли это попытка
        if self.request.retries < self.max_retries:
            # Пока есть попытки — это только предупреждение
            log.warning(
                "task_retry_triggered", error=str(exc), next_attempt=current_attempt + 1
            )
        else:
            # Попытки кончились — это критическая ошибка
            log.error(
                "reminder_task_failed - Ошибка при формировании ежедневных напоминаний",
                error=str(exc),
                exc_info=True,
            )

        # Обязательно пробрасываем ошибку дальше,
        # чтобы autoretry_for поймал её и запланировал перезапуск
        raise exc


@shared_task(
    bind=True,
    max_retries=12,  # Максимум 12 попыток
    retry_backoff=300,  # Первая попытка через 5 минут (300 сек)
    retry_backoff_max=7200,  # Максимальное ожидание 2 часа (7200 сек)
    retry_jitter=True,
    # Добавляет случайный шум (чтобы 100 писем не ударили в сервер в 1 миллисекунду, когда он поднимется)
)
def send_email(self, to: str, subject: str, body: str):
    log = logger.bind(
        recipient=to,
        subject=subject,
        attempt=self.request.retries + 1,
        task_id=self.request.id,
    )
    try:
        log.info("email_delivery_started - Отправка письма")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.EMAIL_HOST_USER
        message["To"] = to
        message.set_content(body)

        send_message(message=message)

        log.info("email_delivery_finished - Письма успешно отправлено")
        return f"Письмо успешно отправлено на {to}"

    except Exception as exc:
        if self.request.retries < self.max_retries:
            log.warning(
                "email_delivery_retry - Повторная отправка письма",
                error=str(exc),
                next_retry_in=300,
            )
        else:
            log.error(
                "email_delivery_final_failure - Ошибка отправка письма",
                error=str(exc),
                exc_info=True,
            )

        raise self.retry(exc=exc)


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, retry_backoff=300)
def notify_user_about_swap(self, date_str, user_id):
    """
    Задача: Уведомить сотрудника, что на него перекинули смену.
    """
    # 1. Добавляем больше контекста в мешок (attempt и task_id)
    current_attempt = self.request.retries + 1
    log = logger.bind(
        task_name=self.name,
        task_id=self.request.id,
        target_date=date_str,
        user_id=user_id,
        attempt=current_attempt,
    )

    log.info("preparing_swap_notification - Подготовка к отправке уведомления")

    try:
        # Выполняем тяжелый запрос к БД
        data = get_swap_notification_data(date_str, user_id)

        if not data:
            log.warning("notification_data_empty - Данные не найдены", data=data)
            return "No email data found"

        log.info(
            "dispatching_send_email_task - Данные готовы, передаем на отправку",
            email=data["email"],
        )

        # Отправляем задачу на реальную доставку письма
        send_email.delay(to=data["email"], subject=data["subject"], body=data["body"])

        log.info("notification_preparation_completed")
        return "Notification task queued"

    except Exception as e:
        # 2. Логируем попытки по нашей стратегии WARNING/ERROR
        if self.request.retries < self.max_retries:
            log.warning(
                "notification_preparation_retry - Ошибка подготовки, повтор",
                error=str(e),
                next_attempt=current_attempt + 1,
            )
        else:
            log.error(
                "notification_preparation_final_failure - Не удалось подготовить уведомление",
                error=str(e),
                exc_info=True,
            )

        # Пробрасываем исключение для срабатывания autoretry_for
        raise e


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, retry_backoff=300)
def notify_admin_about_swap(self, message):
    """Уведомить админа об обмене сменами"""
    log = logger.bind(
        task_name=self.name, task_id=self.request.id, attempt=self.request.retries + 1
    )
    log.info("admin_swap_notification_started")

    try:
        admins = User.objects.filter(role=User.ROLE_ADMIN, is_active=True)
        admin_count = 0

        for admin in admins:
            if admin.email:
                send_email.delay(to=admin.email, subject="Автозамена", body=message)
                admin_count += 1

        log.info("admin_swap_notification_finished", admins_notified=admin_count)
        return f"Notified {admin_count} admins."
    except Exception as e:
        log.error("admin_swap_notification_failed", error=str(e), exc_info=True)
        raise e


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=5, retry_backoff=60)
def task_calculate_score(self, inspection_id):
    """Фоновый расчет баллов за отчет"""
    start_perf = time.perf_counter()
    log = logger.bind(
        task_name=self.name, task_id=self.request.id, inspection_id=inspection_id
    )
    log.info("score_calculation_started")

    try:
        inspection = Inspection.objects.prefetch_related("items").get(id=inspection_id)

        # 1. Считаем баллы
        score = calculate_inspection_score(inspection)
        # 2. Обновляем сводку участка
        calculate_daily_location_scores(inspection.date_check)

        duration = time.perf_counter() - start_perf
        log.info("score_calculation_finished", score=score, duration=round(duration, 4))
        return f"Score: {score}"

    except Inspection.DoesNotExist:
        log.warning("score_calculation_skipped_not_found")
        return "Error: Inspection not found"
    except Exception as e:
        log.error("score_calculation_failed", error=str(e), exc_info=True)
        raise e


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, retry_backoff=300)
def send_weekly_schedule_digest(self):
    """Рассылка плана на следующую неделю (Пятница)"""
    start_perf = time.perf_counter()
    log = logger.bind(task_name=self.name, task_id=self.request.id)
    log.info("weekly_digest_started")

    try:
        emails_to_send = prepare_weekly_notifications()
        if not emails_to_send:
            log.info("weekly_digest_skipped_no_data")
            return "No data for weekly digest"

        success_count = 0
        for data in emails_to_send:
            send_email.delay(
                to=data["recipient_email"], subject=data["subject"], body=data["body"]
            )
            success_count += 1
            log.info("weekly_digest_dispatched", recipient=data["recipient_email"])

        duration = time.perf_counter() - start_perf
        log.info(
            "weekly_digest_finished",
            total_sent=success_count,
            duration=round(duration, 4),
        )
        return f"Sent {success_count} digests"
    except Exception as e:
        log.error("weekly_digest_failed", error=str(e), exc_info=True)
        raise e


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, retry_backoff=300)
def send_monday_reminders(self):
    """Рассылка напоминаний в понедельник утром"""
    log = logger.bind(task_name=self.name, task_id=self.request.id)
    log.info("monday_reminders_started")

    try:
        emails_to_send = prepare_monday_reminders()
        if not emails_to_send:
            log.info("monday_reminders_skipped_no_data")
            return "No data"

        success_count = 0
        for data in emails_to_send:
            send_email.delay(
                to=data["recipient_email"], subject=data["subject"], body=data["body"]
            )
            success_count += 1

        log.info("monday_reminders_finished", total_sent=success_count)
        return f"Queued {success_count} reminders"
    except Exception as e:
        log.error("monday_reminders_failed", error=str(e), exc_info=True)
        raise e


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, retry_backoff=300)
def send_admin_overdue_report(self):
    """Рассылка списка должников администраторам"""
    start_perf = time.perf_counter()
    log = logger.bind(task_name=self.name, task_id=self.request.id)
    log.info("overdue_report_started")

    try:
        report_body = prepare_overdue_notifications()
        if not report_body:
            log.info("overdue_report_skipped_no_overdue")
            return "No overdue reports today"

        admins = User.objects.filter(role=User.ROLE_ADMIN, is_active=True)
        admin_emails = [admin.email for admin in admins if admin.email]

        if not admin_emails:
            log.warning("overdue_report_failed_no_admins")
            return "No admins to notify"

        subject = f"🚨 ВНИМАНИЕ: Незавершенные проверки на {timezone.now().strftime('%d.%m.%Y')}"

        for email in admin_emails:
            send_email.delay(to=email, subject=subject, body=report_body)
            log.info("overdue_report_dispatched", admin_email=email)

        duration = time.perf_counter() - start_perf
        log.info(
            "overdue_report_finished",
            admins_count=len(admin_emails),
            duration=round(duration, 4),
        )
        return f"Report sent to {len(admin_emails)} admins"
    except Exception as e:
        log.error("overdue_report_failed", error=str(e), exc_info=True)
        raise e
