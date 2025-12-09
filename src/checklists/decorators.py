from functools import wraps

from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login


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
        if request.user.is_staff or request.user.role in ["admin", "master"]:
            return view_func(request, *args, **kwargs)

        # Если не прошел проверку - ЖЕСТКИЙ ОТКАЗ
        raise PermissionDenied("Доступ разрешен только администраторам.")

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

        # Проверка: Должен быть именно worker
        if request.user.role == "worker":
            return view_func(request, *args, **kwargs)

        # Админам тут делать нечего
        raise PermissionDenied("Этот раздел только для исполнителей работ.")

    return _wrapped_view
