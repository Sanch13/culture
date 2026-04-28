import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

default_settings = "config.prod"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", default_settings)

app = Celery("culture")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# --- ПОДКЛЮЧАЕМ OPENTELEMETRY ---
# Этот сигнал сработает, когда Celery поднимет "рабочего", готового выполнять задачи.
@worker_process_init.connect(weak=False)
def init_celery_tracing(*args, **kwargs):
    from config.otel import setup_opentelemetry

    setup_opentelemetry()


app.conf.beat_schedule = {
    # Задача 1: Генерация расписания. Запуск каждую пятницу в 14:20
    "generate-schedule-every-friday": {
        "task": "checklists.tasks.task_generate_weekly_schedule",
        # "schedule": crontab(day_of_week="friday", hour=11, minute=00),
        # "schedule": crontab(day_of_week="thursday", hour=9, minute=0),
        "schedule": crontab(day_of_week="*", hour=12, minute=0),  # del
    },
    # Задача 2: Отправка email админу
    # 'send_admin_email': {
    #     'task': 'checklists.tasks.send_admin_email',
    #     'schedule': crontab(hour=15, minute=18, day_of_week='mon-fri'),
    # },
    # Задача 3: Ежедневная отправка email с напоминанием о проверке (9 утра)
    "send_inspection_reminders": {
        "task": "checklists.tasks.send_inspection_reminders",
        # "schedule": crontab(day_of_week="*", hour=9, minute=0),
        "schedule": crontab(day_of_week="*", hour=12, minute=10),  # del
    },
    # Задача 4: Отправка email (в Пт 11:20) всем проверяющим на след. неделе
    "send-weekly-digest-friday": {
        "task": "checklists.tasks.send_weekly_schedule_digest",
        # "schedule": crontab(day_of_week="friday", hour=11, minute=20),
        # "schedule": crontab(day_of_week="*", hour=10, minute=20),
        "schedule": crontab(day_of_week="*", hour=12, minute=15),  # del
    },
    # Задача 5: Напоминание в пн (09:00) всем проверяющим на этой неделе с вт
    "send-monday-reminders": {
        "task": "checklists.tasks.send_monday_reminders",
        # "schedule": crontab(day_of_week="monday", hour=9, minute=0),
        "schedule": crontab(day_of_week="*", hour=12, minute=5),  # del
    },
    # Задача 6: Письмо для админов по должникам за день (Ежедневно в 14:00)
    "send-admin-overdue-report": {
        "task": "checklists.tasks.send_admin_overdue_report",
        # Запускаем каждый день (Пн-Вс).
        # Если сегодня выходной, функция is_working_day внутри сервиса сама отменит отправку.
        # "schedule": crontab(hour=14, minute=0),
        "schedule": crontab(day_of_week="*", hour=12, minute=20),  # del
    },
    # "sync-mailcow-emails": {
    #     "task": "users.tasks.task_sync_corporate_emails",
    #     # Запускаем каждую ночь в 02:00
    #     "schedule": crontab(hour=2, minute=0),
    #     # Для тестирования сейчас можешь раскомментировать эту строку:
    #     # 'schedule': crontab(minute='*/5'),  # Каждые 5 минут
    # },
}
