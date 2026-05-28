import pytest
import datetime
from django.utils import timezone
from checklists.models import Schedule
from checklists.services.services import perform_auto_swap
from checklists.services.cascade_shift_schedule import cascade_shift_schedule


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


@pytest.mark.django_db
def test_cascade_shift_ignores_swapped_but_updates_it(inspectors, test_route):
    """Проверка каскадного сдвига: удаление зафиксированной смены (is_swapped=True)"""
    u1, u2, u3 = inspectors[0], inspectors[1], inspectors[2]
    d1 = timezone.now().date() + datetime.timedelta(days=1)
    d2 = d1 + datetime.timedelta(days=1)
    d3 = d2 + datetime.timedelta(days=1)

    t1 = test_route.templates.first()

    # u1 (заблокирован), u2 (свободен), u3 (свободен)
    s1 = Schedule.objects.create(date=d1, inspector=u1, template=t1, is_swapped=True)
    s2 = Schedule.objects.create(date=d2, inspector=u2, template=t1, is_swapped=False)
    s3 = Schedule.objects.create(date=d3, inspector=u3, template=t1, is_swapped=False)

    # Админ удаляет u1 (у которого is_swapped=True)
    success, msg = cascade_shift_schedule(d1, u1, requestor=u1, is_silent=True)

    assert success is True

    s1.refresh_from_db()
    s2.refresh_from_db()

    # Смена s1 должна перейти к u2, а статус is_swapped должен стать False!
    assert s1.inspector == u2
    assert s1.is_swapped is False

    # Смена s2 должна перейти к u3
    assert s2.inspector == u3

    # Смена s3 должна быть удалена (последняя в цепочке)
    assert not Schedule.objects.filter(id=s3.id).exists()
