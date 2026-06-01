import pytest
import datetime
from django.db.models import Count, Q
from django.contrib.auth import get_user_model
from checklists.models import Inspection

User = get_user_model()


@pytest.mark.django_db
def test_dashboard_distinct_inspection_counts(inspectors, test_route):
    """
    Проверяем, что два отчета в один день считаются как ОДИН выход (distinct=True).
    """
    user = inspectors[0]
    tmpl1 = test_route.templates.first()
    tmpl2 = test_route.templates.last()

    date_day_1 = datetime.date(2026, 5, 10)
    date_day_2 = datetime.date(2026, 5, 15)

    # Создаем 2 отчета в ОДИН день (День 1)
    Inspection.objects.create(
        inspector=user, template=tmpl1, date_check=date_day_1, is_completed=True
    )
    Inspection.objects.create(
        inspector=user, template=tmpl2, date_check=date_day_1, is_completed=True
    )

    # Создаем 1 отчет в ДРУГОЙ день (День 2)
    Inspection.objects.create(
        inspector=user, template=tmpl1, date_check=date_day_2, is_completed=True
    )

    # Имитируем запрос из View с использованием distinct=True
    stats = (
        User.objects.filter(id=user.id)
        .annotate(
            completed_count=Count(
                "inspection__date_check",  # Считаем уникальные даты!
                distinct=True,
                filter=Q(inspection__is_completed=True),
            )
        )
        .first()
    )

    # ОЖИДАНИЕ: Отчетов 3, но выходов на работу (уникальных дат) - 2!
    assert stats.completed_count == 2
