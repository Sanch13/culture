import pytest
from checklists.models import Inspection, InspectionItem, InspectionSectionScore
from checklists.services.services import calculate_inspection_score


@pytest.mark.django_db
def test_calculate_score_with_progressive_penalty(inspectors, test_route):
    """Тест прогрессивных штрафов за повторные нарушения"""
    user = inspectors[0]
    template = test_route.templates.first()

    # Создаем отчет
    inspection = Inspection.objects.create(
        template=template, inspector=user, date_check="2026-05-10", is_completed=True
    )

    # Создаем 2 пункта (оба в разделе "A")
    # Пункт 1: ОК
    InspectionItem.objects.create(
        inspection=inspection,
        section_name="Раздел A",
        section_type="A",
        is_compliant=True,
        consecutive_violations=0,
    )

    # Пункт 2: Повторное нарушение (рецидив 2-го уровня = балл 3.0)
    _ = InspectionItem.objects.create(
        inspection=inspection,
        section_name="Раздел A",
        section_type="A",
        is_compliant=False,
        is_repeated_violation=True,
        consecutive_violations=2,
    )

    # 1. Запускаем калькулятор
    final_score = calculate_inspection_score(inspection)

    # 2. Проверяем балл раздела "A"
    section_score = InspectionSectionScore.objects.get(
        inspection=inspection, section_type="A"
    )
    assert section_score.score == 3.0  # Штраф за рецидив уровня 2

    # 3. Проверяем балл отчета (должен обвалиться до балла самого плохого раздела со штрафом)
    assert final_score == 3.0
    assert inspection.final_score == 3.0


@pytest.mark.django_db
def test_calculate_score_normal_math(inspectors, test_route):
    """Тест обычной математики без рецидивов: 6 * ((Всего - Нарушений) / Всего)"""
    inspection = Inspection.objects.create(
        template=test_route.templates.first(),
        inspector=inspectors[0],
        date_check="2026-05-11",
    )

    # Раздел C: 4 вопроса, 1 нарушение. Формула: 6 * (3/4) = 4.5
    for i in range(3):
        InspectionItem.objects.create(
            inspection=inspection, section_type="C", is_compliant=True
        )
    InspectionItem.objects.create(
        inspection=inspection,
        section_type="C",
        is_compliant=False,
        consecutive_violations=1,
    )

    final_score = calculate_inspection_score(inspection)

    assert final_score == 4.5
