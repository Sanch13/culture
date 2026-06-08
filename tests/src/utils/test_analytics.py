import pytest
from checklists.utils import get_previous_month_averages  # Замени на свой путь
from checklists.models import LocationDailyScore, Inspection, InspectionSectionScore


@pytest.mark.django_db
def test_get_previous_month_averages_year_crossover(inspectors, test_route):
    """
    Проверяем, что если мы запрашиваем аналитику за Январь 2026,
    система правильно берет данные за Декабрь 2025.
    """
    loc = test_route.templates.first().location
    user = inspectors[0]  # Берем любого пользователя

    # 1. Создаем данные за ПРОШЛЫЙ месяц (Декабрь 2025)
    LocationDailyScore.objects.create(location=loc, date="2025-12-10", score=4.0)
    LocationDailyScore.objects.create(location=loc, date="2025-12-15", score=6.0)

    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # Создаем фейковый отчет за декабрь
    inspection = Inspection.objects.create(
        template=test_route.templates.first(),
        inspector=user,
        date_check="2025-12-05",
        is_completed=True,
    )

    # Теперь привязываем оценку раздела к этому отчету
    InspectionSectionScore.objects.create(
        inspection=inspection,  # <--- Передали созданный отчет!
        date_check="2025-12-05",
        section_type="B1",
        score=3.0,
    )
    # -----------------------

    # 2. Вызываем функцию за ТЕКУЩИЙ месяц (Январь 2026)
    loc_map, tmpl_map, b1_avg = get_previous_month_averages(year=2026, month=1)

    # 3. Проверяем результаты
    # Средний балл участка за декабрь: (4.0 + 6.0) / 2 = 5.0
    assert loc_map.get(loc.id) == 5.0

    # Глобальный B1: был штраф 3.0, значит весь день должен быть 3.0
    assert b1_avg == 3.0
