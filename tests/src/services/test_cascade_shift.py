import pytest
import datetime
from django.utils import timezone
from checklists.models import Schedule
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
