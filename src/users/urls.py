from django.urls import path, reverse_lazy

from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)

from users import views
from users.forms import CustomAuthenticationForm

app_name = "users"

urlpatterns = [
    # Регистрация
    path("register/", views.register, name="register"),
    # Вход (Используем готовую View от Django, но подсовываем нашу форму)
    path(
        "login/",
        LoginView.as_view(
            template_name="users/login.html",
            authentication_form=CustomAuthenticationForm,
        ),
        name="login",
    ),
    # Выход
    path("logout/", LogoutView.as_view(next_page="users:login"), name="logout"),
    # --- СБРОС ПАРОЛЯ (4 шага) ---
    # 1. Форма ввода Email
    path(
        "password-reset/",
        PasswordResetView.as_view(
            template_name="users/password_reset.html",
            email_template_name="users/password_reset_email.html",  # <--- Текст самого письма
            success_url=reverse_lazy("users:password_reset_done"),
        ),
        name="password_reset",
    ),
    # 2. Уведомление "Письмо отправлено"
    path(
        "password-reset/done/",
        PasswordResetDoneView.as_view(template_name="users/password_reset_done.html"),
        name="password_reset_done",
    ),
    # 3. Ссылка из письма (ввод нового пароля)
    path(
        "password-reset-confirm/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(
            template_name="users/password_reset_confirm.html",
            success_url=reverse_lazy("users:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    # 4. Финиш "Пароль изменен"
    path(
        "password-reset-complete/",
        PasswordResetCompleteView.as_view(
            template_name="users/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
