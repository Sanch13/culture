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

        # Здесь оставляем ТОЛЬКО плейсхолдеры (примеры ввода)
        # Классы стилей мы добавим ниже, в __init__
        widgets = {
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "375291112233",
                    # type="tel" включает цифровую клавиатуру на телефоне
                    "type": "tel",
                    # inputmode="numeric" — дополнительная подсказка браузеру
                    "inputmode": "numeric",
                    "maxlength": "15",
                }
            ),
            "email": forms.EmailInput(attrs={"placeholder": "name@example.com"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Иван"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Иванов"}),
        }

        help_texts = {
            "phone": "Только цифры (9-15 знаков).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- МАГИЯ СТИЛЕЙ ---
        # Проходим по всем полям (включая Пароли!) и добавляем класс Bootstrap
        for field_name, field in self.fields.items():
            # Получаем текущие классы (если есть) и добавляем form-control
            # Если поле это Checkbox (на будущее), ему нужен другой класс
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"

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
