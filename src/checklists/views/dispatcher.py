from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib.auth import get_user_model

from checklists.context_processors import BOSS_ROLES

User = get_user_model()


# --- ГЛАВНЫЙ ВХОД (Диспетчер) ---
@login_required
def index_dispatcher(request):
    """
    Единственное место, где мы решаем, кого куда послать при входе.
    """
    user = request.user

    if user.role == User.ROLE_ADMIN or user.is_staff:
        return redirect("admin_dashboard")

    elif user.role in BOSS_ROLES or user.role == User.ROLE_WORKER:
        return redirect("employee_dashboard")
    else:
        # Если роль не задана - кидаем на страницу входа или 403
        return redirect("users:login")
