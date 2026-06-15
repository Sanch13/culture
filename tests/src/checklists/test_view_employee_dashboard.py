import pytest
import datetime
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone
from checklists.models import Schedule


# Вспомогательная функция для создания "правильного" времени с часовым поясом
def create_mock_time(year, month, day, hour, minute):
    dt = datetime.datetime(year, month, day, hour, minute)
    return timezone.make_aware(dt, timezone.get_default_timezone())


# =====================================================================
# ТЕСТЫ ГОРИЗОНТА ПЛАНИРОВАНИЯ (ПОКАЗ СЛЕДУЮЩЕЙ НЕДЕЛИ)
# =====================================================================


@pytest.mark.django_db
@patch(
    "checklists.views.employee_cabinet.timezone.now"
)  # Подменяем функцию now() внутри твоего файла views.py
def test_horizon_wednesday(mock_now, client, inspectors, test_route):
    """Среда: Проверяющий видит задачи ТОЛЬКО до конца текущей недели"""
    # Устанавливаем фейковое время: Среда, 13 мая 2026, 14:00
    mock_now.return_value = create_mock_time(2026, 5, 13, 14, 0)

    user = inspectors[0]
    client.force_login(user)
    tmpl = test_route.templates.first()

    # Задача на завтра (Четверг, текущая неделя)
    task_thursday = Schedule.objects.create(
        date=datetime.date(2026, 5, 14), inspector=user, template=tmpl
    )
    # Задача на следующий Вторник (След. неделя)
    # task_next_tuesday = Schedule.objects.create(
    #     date=datetime.date(2026, 5, 19), inspector=user, template=tmpl
    # )  # noqa: F841

    response = client.get(reverse("employee_dashboard"))
    future_tasks = list(response.context["future_tasks"])

    # Ожидание: Видна только задача на четверг. Задача на следующий вторник скрыта.
    assert len(future_tasks) == 1
    assert future_tasks[0] == task_thursday


@pytest.mark.django_db
@patch("checklists.views.employee_cabinet.timezone.now")
def test_horizon_friday_before_11_20(mock_now, client, inspectors, test_route):
    """Пятница до 11:20: Задачи на следующую неделю скрыты"""
    # Устанавливаем фейковое время: Пятница, 15 мая 2026, 11:15
    mock_now.return_value = create_mock_time(2026, 5, 15, 11, 15)

    user = inspectors[0]
    client.force_login(user)

    # Создаем задачу на следующий Вторник (19 мая)
    Schedule.objects.create(
        date=datetime.date(2026, 5, 19),
        inspector=user,
        template=test_route.templates.first(),
    )

    response = client.get(reverse("employee_dashboard"))

    # Ожидание: future_tasks пуст, потому что 11:20 еще не наступило
    assert list(response.context["future_tasks"]) == []


@pytest.mark.django_db
@patch("checklists.views.employee_cabinet.timezone.now")
def test_horizon_friday_exactly_at_11_20(mock_now, client, inspectors, test_route):
    """Пятница ровно в 11:20: Горизонт расширяется, задачи видны"""
    # Устанавливаем фейковое время: Пятница, 15 мая 2026, 11:20
    mock_now.return_value = create_mock_time(2026, 5, 15, 11, 20)

    user = inspectors[0]
    client.force_login(user)

    task_next_tuesday = Schedule.objects.create(
        date=datetime.date(2026, 5, 19),
        inspector=user,
        template=test_route.templates.first(),
    )

    response = client.get(reverse("employee_dashboard"))

    # Ожидание: 11:20 наступило, горизонт расширился, задача появилась
    assert len(response.context["future_tasks"]) == 1
    assert response.context["future_tasks"][0] == task_next_tuesday


@pytest.mark.django_db
@patch("checklists.views.employee_cabinet.timezone.now")
def test_horizon_saturday(mock_now, client, inspectors, test_route):
    """Суббота: Горизонт расширен на следующую неделю в любое время"""
    # Устанавливаем фейковое время: Суббота, 16 мая 2026, 09:00 утра
    mock_now.return_value = create_mock_time(2026, 5, 16, 9, 0)

    user = inspectors[0]
    client.force_login(user)

    task_next_tuesday = Schedule.objects.create(
        date=datetime.date(2026, 5, 19),
        inspector=user,
        template=test_route.templates.first(),
    )

    response = client.get(reverse("employee_dashboard"))

    # Ожидание: В субботу время не важно, горизонт расширен
    assert len(response.context["future_tasks"]) == 1
    assert response.context["future_tasks"][0] == task_next_tuesday
