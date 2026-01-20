from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


# --- ГЛАВНЫЙ ВХОД (Диспетчер) ---
@login_required
def index_dispatcher(request):
    """
    Единственное место, где мы решаем, кого куда послать при входе.
    """
    if request.user.role in ["admin", "master"] or request.user.is_staff:
        return redirect("admin_dashboard")
    elif request.user.role == "worker":
        return redirect("employee_dashboard")
    else:
        # Если роль не задана - кидаем на страницу входа или 403
        return redirect("users:login")
