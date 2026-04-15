from django.contrib.auth import get_user_model

User = get_user_model()

# Список ролей-руководителей
BOSS_ROLES = [
    User.ROLE_MANAGER,
    User.ROLE_MASTER,
    User.ROLE_SENIOR_MASTER,
    User.ROLE_PRODUCTION_CHIEF,
    User.ROLE_DEPUTY,
    User.ROLE_OBSERVER,
    User.ROLE_ADMIN,
]


def boss_context(request):
    """
    Добавляет переменную is_boss во все шаблоны.
    """
    # if request.user.is_authenticated:
    if hasattr(request, "user") and request.user.is_authenticated:
        return {"is_boss": request.user.role in BOSS_ROLES or request.user.is_staff}
    return {"is_boss": False}
