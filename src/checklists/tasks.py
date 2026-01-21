from datetime import timedelta

from django.utils import timezone

from celery import shared_task

from src.checklists.services import generate_schedule


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
