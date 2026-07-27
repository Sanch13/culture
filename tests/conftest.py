# tests/conftest.py
import pytest
from django.contrib.auth import get_user_model
from checklists.models import Location, ChecklistTemplate, InspectionRoute

User = get_user_model()


@pytest.fixture
def valid_form_data():
    """Базовый набор корректных данных без отпуска"""
    return {
        "first_name": "Иван",
        "last_name": "Иванов",
        "middle_name": "Иванович",
        "phone": "375291234567",
        "vacation_start": "",
        "vacation_end": "",
    }


@pytest.fixture
def inspectors(db):
    """Создает 5 инспекторов с ID от 1 до 5"""
    users = []
    for i in range(1, 6):
        user = User.objects.create_user(
            email=f"inspector_{i}@miran-bel.com",
            password="Password123",  # pragma: allowlist secret
            can_perform_inspections=True,
            is_active=True,
        )
        # Искусственно задаем ID для предсказуемости тестов
        User.objects.filter(id=user.id).update(id=i)
        user = User.objects.get(id=i)
        users.append(user)
    return users


@pytest.fixture
def test_route(db):
    """Создает тестовый маршрут с 2 шаблонами"""
    loc = Location.objects.create(name="Участок Сборки")
    tmpl1 = ChecklistTemplate.objects.create(name="Сборка 1", location=loc)
    tmpl2 = ChecklistTemplate.objects.create(name="Сборка 2", location=loc)

    route = InspectionRoute.objects.create(title="Маршрут Сборка", order=1)
    route.templates.add(tmpl1, tmpl2)
    return route
