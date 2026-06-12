import pytest
from users.forms import UserProfileForm


@pytest.mark.django_db
def test_form_valid_basic_data(valid_form_data):
    """Форма должна быть валидна при правильных данных без отпуска"""
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is True


@pytest.mark.django_db
def test_form_invalid_missing_required(valid_form_data):
    """Форма не валидна без обязательных полей (Имя, Фамилия)"""
    valid_form_data.pop("first_name")
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    assert "first_name" in form.errors


@pytest.mark.django_db
def test_form_clean_phone_empty_string(valid_form_data):
    """Метод clean_phone должен превращать пустую строку в None"""
    valid_form_data["phone"] = ""
    form = UserProfileForm(data=valid_form_data)

    assert form.is_valid() is True
    # Проверяем, что пустая строка превратилась в None
    assert form.cleaned_data["phone"] is None


@pytest.mark.django_db
def test_form_invalid_phone_format(valid_form_data):
    """Проверка валидатора Regex телефона из модели"""
    valid_form_data["phone"] = "abc123"  # Буквы
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    assert "phone" in form.errors

    valid_form_data["phone"] = "123"  # Меньше 9 цифр
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False


# ==========================================
# ТЕСТЫ ЛОГИКИ ОТПУСКА (МЕТОД CLEAN)
# ==========================================


@pytest.mark.django_db
def test_form_valid_with_correct_vacation(valid_form_data):
    """Корректный отпуск (например, 10 дней) должен проходить валидацию"""
    valid_form_data["vacation_start"] = "2026-06-01"
    valid_form_data["vacation_end"] = "2026-06-10"

    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is True


@pytest.mark.django_db
def test_form_vacation_missing_one_date(valid_form_data):
    """Ошибка: Заполнена только одна дата отпуска"""
    # Только старт
    valid_form_data["vacation_start"] = "2026-06-01"
    valid_form_data["vacation_end"] = ""
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    # Теперь ошибка лежит конкретно в поле vacation_end
    assert "Укажите конец отпуска" in form.errors["vacation_end"][0]

    # Только конец
    valid_form_data["vacation_start"] = ""
    valid_form_data["vacation_end"] = "2026-06-10"
    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    # Теперь ошибка лежит конкретно в поле vacation_start
    assert "Укажите начало отпуска" in form.errors["vacation_start"][0]


@pytest.mark.django_db
def test_form_vacation_end_before_start(valid_form_data):
    """Ошибка: Дата конца раньше даты начала"""
    valid_form_data["vacation_start"] = "2026-06-10"
    valid_form_data["vacation_end"] = "2026-06-01"  # В прошлом

    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    # Мы привязали эту ошибку к vacation_end
    assert "не может быть раньше" in form.errors["vacation_end"][0]


@pytest.mark.django_db
def test_form_vacation_exceeds_30_days(valid_form_data):
    """Ошибка: Отпуск больше 30 дней"""
    valid_form_data["vacation_start"] = "2026-06-01"
    valid_form_data["vacation_end"] = "2026-07-01"  # 31 день

    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is False
    # Мы привязали эту ошибку к vacation_end
    assert "не может превышать 30 дней" in form.errors["vacation_end"][0]


@pytest.mark.django_db
def test_form_vacation_exactly_30_days(valid_form_data):
    """Крайний случай: Отпуск ровно 30 дней должен быть валиден"""
    valid_form_data["vacation_start"] = "2026-06-01"
    # С 1 июня по 30 июня = ровно 30 дней
    valid_form_data["vacation_end"] = "2026-06-30"

    form = UserProfileForm(data=valid_form_data)
    assert form.is_valid() is True
