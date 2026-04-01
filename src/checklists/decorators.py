from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.contrib import messages

from checklists.context_processors import BOSS_ROLES

User = get_user_model()


def admin_required(view_func):
    """
    Декоратор: Пускает только Админов и Мастеров.
    Если заходит обычный рабочий -> Ошибка 403.
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # Проверяем, авторизован ли вообще
        if not request.user.is_authenticated:
            # Пусть settings.LOGIN_URL сам разбирается с анонимами
            return redirect_to_login(request.get_full_path())

        # Главная проверка: Роль или статус Staff
        if request.user.is_staff or request.user.role == User.ROLE_ADMIN:
            return view_func(request, *args, **kwargs)

        # --- МЯГКИЙ ОТКАЗ ---
        messages.warning(request, "Доступ в раздел Администратора запрещен.")
        return redirect("employee_dashboard")

    return _wrapped_view


def employee_required(view_func):
    """
    Декоратор: Пускает ТОЛЬКО обычных сотрудников (role='worker').
    Админов не пускает (если только мы не хотим, чтобы админ мог притворяться рабочим).
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        if request.user.role == User.ROLE_WORKER:
            return view_func(request, *args, **kwargs)

        return view_func(request, *args, **kwargs)

    return _wrapped_view


def management_required(view_func):
    """
    Декоратор: Пускает только руководителей (любая роль из BOSS_ROLES) или staff.
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # 1. Проверка на авторизацию
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        # 2. Проверка на босса
        if request.user.role in BOSS_ROLES or request.user.is_staff:
            return view_func(request, *args, **kwargs)

        # --- МЯГКИЙ ОТКАЗ ---
        messages.warning(request, "Этот раздел доступен только руководителям.")
        return redirect("employee_dashboard")

    return _wrapped_view
