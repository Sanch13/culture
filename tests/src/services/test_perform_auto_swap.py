import pytest
import datetime
from django.utils import timezone
from checklists.models import Schedule
from checklists.services.services import perform_auto_swap


@pytest.mark.django_db
def test_perform_auto_swap_correct_future_date(inspectors, test_route):
    """Проверка автозамены: алгоритм должен искать замену ПОСЛЕ даты самой смены"""
    user1 = inspectors[0]
    user2 = inspectors[1]

    # 1. Создаем смену, от которой отказываются: Вторник (текущая неделя)
    today = timezone.now().date()
    # Сдвигаем "сегодня" искусственно на Вторник для предсказуемости
    current_tuesday = (
        today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=1)
    )

    t1 = test_route.templates.first()
    schedule_to_swap = Schedule.objects.create(
        date=current_tuesday, inspector=user1, template=t1
    )

    # 2. Создаем кандидата: Вторник СЛЕДУЮЩЕЙ недели
    next_tuesday = current_tuesday + datetime.timedelta(days=7)
    target_schedule = Schedule.objects.create(
        date=next_tuesday, inspector=user2, template=t1
    )

    # 3. Вызываем автозамену
    success, msg, info = perform_auto_swap(schedule_to_swap, "sick", "Заболел")

    # 4. Проверяем результаты
    assert success is True

    schedule_to_swap.refresh_from_db()
    target_schedule.refresh_from_db()

    # user2 теперь должен работать в первый Вторник
    assert schedule_to_swap.inspector == user2
    assert schedule_to_swap.is_swapped is False  # Целевому юзеру можно отказываться

    # user1 теперь работает в следующий Вторник
    assert target_schedule.inspector == user1
    assert target_schedule.is_swapped is True  # Инициатору отказываться больше нельзя
