def is_privileged_user(user):
    """
    Возвращает True, если пользователю разрешен доступ в админ-панель.
    Это Админы, Мастера или Staff.
    """
    return user.is_staff or user.role in ["admin", "master"]
