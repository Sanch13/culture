import pytest
import datetime
from checklists.models import InspectorRouteStat, Schedule
from checklists.services.gen_schedule_with_balance import generate_schedule


@pytest.mark.django_db
def test_generator_uses_smart_counters(inspectors, test_route):
    """
    Проверяем, что генератор отдаст маршрут тому, кто был на нем реже.
    """
    # test_route - это 1 маршрут. inspectors - это 5 человек.
    user1 = inspectors[0]
    user2 = inspectors[1]

    # Создаем фейковую историю в БД:
    # user1 был на этом маршруте 10 раз, а user2 - 0 раз.
    InspectorRouteStat.objects.create(
        inspector=user1, route=test_route, visits_count=10
    )
    InspectorRouteStat.objects.create(inspector=user2, route=test_route, visits_count=0)

    # Запускаем генерацию на 1 рабочий день
    start_date = datetime.date(2026, 5, 11)  # Понедельник
    generate_schedule(start_date=start_date, days_count=1)

    # Проверяем, кому досталась смена
    schedules = Schedule.objects.filter(date=start_date)
    assert schedules.exists()

    # Смена ОБЯЗАТЕЛЬНО должна достаться user2, так как его счетчик равен 0!
    for s in schedules:
        assert s.inspector == user2

    # Проверяем, что счетчик в БД для user2 увеличился на 1
    stat_user2 = InspectorRouteStat.objects.get(inspector=user2, route=test_route)
    assert stat_user2.visits_count == 1
