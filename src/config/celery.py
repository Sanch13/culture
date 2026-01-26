import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.dev")

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
}
