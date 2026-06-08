import pytest
from unittest.mock import patch
from django.urls import reverse
from checklists.models import Inspection, InspectionItem
from checklists.tasks import task_calculate_score  # Твой путь к celery-таске


@pytest.mark.django_db
@patch("checklists.tasks.task_calculate_score.delay")
def test_admin_edit_inspection_triggers_celery(
    mock_celery_task, client, inspectors, test_route
):
    """
    Проверяем, что сохранение формы админом вызывает пересчет баллов.
    """
    admin_user = inspectors[0]

    # 1. ГАРАНТИРУЕМ, что этот юзер - Админ.
    # (Подставь то поле, по которому твой декоратор @admin_required проверяет права)
    admin_user.role = "admin"
    admin_user.is_superuser = True
    admin_user.save()

    client.force_login(admin_user)

    # 2. Создаем отчет и вопрос (Item)
    inspection = Inspection.objects.create(
        template=test_route.templates.first(),
        inspector=inspectors[1],
        is_completed=True,
    )
    # Важно: задаем is_compliant=True изначально
    item = InspectionItem.objects.create(
        inspection=inspection, is_compliant=True, comment=""
    )

    # 3. Имитируем отправку POST запроса администратором
    url = reverse("admin_inspection_edit", args=[inspection.id])

    # Имена ключей должны строго совпадать с тем, что ждет admin_inspection_edit_view!
    # Вьюха ждет: request.POST.get(f"status_{item.id}")
    post_data = {
        f"status_{item.id}": "false",
        f"comment_{item.id}": "Исправлено админом",
    }

    response = client.post(url, post_data)

    # 4. Проверяем, что нас не выкинуло из-за прав
    # Ожидаем редирект именно на детальную страницу отчета (admin_report_detail)
    assert response.status_code == 302, (
        f"Ожидался редирект, но получили {response.status_code}"
    )
    expected_url = reverse("admin_history")
    assert response.url == expected_url, (
        f"Неправильный редирект. Улетели на {response.url}"
    )

    # 5. Проверяем, что БД обновилась
    item.refresh_from_db()
    assert item.is_compliant is False, "Статус не изменился на False!"
    assert item.comment == "Исправлено админом", "Комментарий не сохранился!"

    # 6. Проверяем, что Celery-таска была вызвана с ID этого отчета
    mock_celery_task.assert_called_once_with(inspection.id)


@pytest.mark.django_db
@patch("checklists.tasks.cache.delete")  # Подменяем метод Redis
@patch("checklists.tasks.calculate_inspection_score")  # Глушим саму математику
@patch("checklists.tasks.calculate_daily_location_scores")
def test_celery_task_clears_redis_cache(
    mock_calc_loc, mock_calc_insp, mock_cache_delete, inspectors, test_route
):
    """
    Проверяем, что таска пересчета очищает аналитический кэш Redis.
    """
    inspection = Inspection.objects.create(
        template=test_route.templates.first(),
        inspector=inspectors[0],
        date_check="2026-05-15",  # Год 2026, Месяц 05
    )

    # Запускаем функцию таски напрямую (без Celery worker)
    # Передаем None вместо self, так как мы тестируем суть логики
    task_calculate_score.run(inspection.id)

    # Ожидаем, что кэш был удален ИМЕННО по ключу "analytics_2026_05"
    mock_cache_delete.assert_called_once_with("analytics_2026_05")
