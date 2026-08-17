import pytest
import datetime
from django.utils import timezone
from checklists.models import Schedule, InspectionRoute, ChecklistTemplate
from checklists.services.cascade_shift_schedule import cascade_shift_schedule


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

    # ИЗМЕНЕНИЕ: Передаем target_schedule=s1 вместо (d1, u1)
    success, msg = cascade_shift_schedule(
        target_schedule=s1, requestor=u1, is_silent=True
    )

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


@pytest.mark.django_db
def test_cascade_shift_global_queue_behavior(inspectors, test_route):
    """
    ПРОВЕРКА ГЛОБАЛЬНОЙ ОЧЕРЕДИ:
    Если u1 стоит на Маршрут 1 и Маршрут 2 в один день,
    то при его удалении с Маршрута 1, он съедет с Маршрута 2 на Маршрут 1.
    """
    u1, u2, u3 = inspectors[0], inspectors[1], inspectors[2]
    d1 = timezone.now().date() + datetime.timedelta(days=1)

    t1 = test_route.templates.first()
    t2 = ChecklistTemplate.objects.create(name="ЭМО Шаблон", location=t1.location)
    route2 = InspectionRoute.objects.create(title="Маршрут ЭМО", order=2)
    route2.templates.add(t2)

    # ОЧЕРЕДЬ ИЗ 4 ШАГОВ:
    # 0: d1, Маршрут 1, u1
    # 1: d1, Маршрут 2, u1
    # 2: d2, Маршрут 1, u2
    # 3: d3, Маршрут 1, u3
    s_route1 = Schedule.objects.create(date=d1, inspector=u1, template=t1)
    s_route2 = Schedule.objects.create(date=d1, inspector=u1, template=t2)

    d2 = d1 + datetime.timedelta(days=1)
    d3 = d2 + datetime.timedelta(days=1)
    s_next_day = Schedule.objects.create(date=d2, inspector=u2, template=t1)
    Schedule.objects.create(date=d3, inspector=u3, template=t1)  # Хвост

    # ДЕЙСТВИЕ: Админ сдвигает самую первую запись (Маршрут 1)
    success, msg = cascade_shift_schedule(
        target_schedule=s_route1, requestor=u1, is_silent=True
    )

    assert success is True

    s_route1.refresh_from_db()
    s_route2.refresh_from_db()
    s_next_day.refresh_from_db()

    # ПРОВЕРЯЕМ БИЗНЕС-ЛОГИКУ (Сдвиг сосиски):
    # На Маршрут 1 u1 приехал сам с себя (со второго своего маршрута)
    assert s_route1.inspector == u1

    # На Маршрут 2 приехал u2
    assert s_route2.inspector == u2

    # На место u2 приехал u3
    assert s_next_day.inspector == u3
