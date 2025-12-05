from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from users.models import User


class CustomUserCreationForm(UserCreationForm):
    """
    Форма регистрации.
    Наследуется от стандартной, но:
    1. Работает с нашей моделью User (по email).
    2. Добавляет поля Имя, Фамилия, Телефон.
    """

    class Meta:
        model = User
        # Перечисляем поля, которые пользователь должен заполнить
        fields = ("email", "first_name", "last_name", "phone")

    def clean_phone(self):
        """
        Дополнительная ручная валидация телефона.
        Превращаем пустую строку в None, чтобы работало unique=True + null=True
        """
        phone = self.cleaned_data.get("phone")
        if not phone:
            return None  # Записываем NULL в базу вместо пустой строки
        return phone


class CustomAuthenticationForm(AuthenticationForm):
    """
    Форма входа.
    Стандартная Django форма использует 'username',
    но так как мы настроили USERNAME_FIELD = 'email',
    она автоматически будет ждать email в поле username.
    Мы просто можем настроить внешний вид (CSS) здесь.
    """

    username = forms.CharField(
        label="Email", widget=forms.TextInput(attrs={"class": "form-control"})
    )
    password = forms.CharField(
        label="Пароль", widget=forms.PasswordInput(attrs={"class": "form-control"})
    )
