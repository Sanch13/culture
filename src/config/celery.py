import os
from celery import Celery
from celery.schedules import crontab

default_settings = "config.prod"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", default_settings)

app = Celery("culture")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Задача 1: Генерация расписания
    "generate-schedule-every-friday": {
        "task": "checklists.tasks.task_generate_weekly_schedule",
        # Запуск каждую пятницу в 18:00
        # 'schedule': crontab(day_of_week='wednesday', hour=12, minute=38),
        "schedule": crontab(day_of_week="friday", hour=18, minute=00),
    },
    # Задача 2: (Пример на будущее) Отправка email
    # 'send_admin_email': {
    #     'task': 'checklists.tasks.send_admin_email',
    #     'schedule': crontab(hour=15, minute=18, day_of_week='mon-fri'),
    # },
    # Задача 3: Ежедневная отправка email с напоминанием о проверке (9 утра)
    "send_inspection_reminders": {
        "task": "checklists.tasks.send_inspection_reminders",
        "schedule": crontab(hour=9, minute=0, day_of_week="mon-fri"),
    },
}
