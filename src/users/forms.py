from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm


User = get_user_model()


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

    def clean_email(self):
        """
        Многоуровневая валидация Email.
        """
        email = self.cleaned_data.get("email")
        if not email:
            return email

        email = email.lower().strip()  # Приводим к нижнему регистру и убираем пробелы

        # 1. ПРОВЕРКА ДОМЕНА (Отсекаем gmail, yandex и т.д.)
        if not email.endswith("@miran-bel.com"):
            raise forms.ValidationError(
                "Регистрация разрешена только для корпоративной почты @miran-bel.com"
            )

        # 2. ПРОВЕРКА НА СУЩЕСТВОВАНИЕ В СИСТЕМЕ (Уже зарегистрирован)
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                "Пользователь с таким Email уже зарегистрирован."
            )

        # 3. ПРОВЕРКА ПО БЕЛОМУ СПИСКУ (Mailcow)
        # Если почты нет в таблице разрешенных - запрещаем регистрацию
        # if not AllowedCorporateEmail.objects.filter(email=email).exists():
        #     raise forms.ValidationError(
        #         "Ваш Email не найден в корпоративном справочнике. "
        #         "Если вы новый сотрудник, дождитесь синхронизации баз данных или обратитесь к администратору."
        #     )

        return email


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

    def clean_username(self):
        """Переводим email в нижний регистр при входе."""
        username = self.cleaned_data.get("username")
        if username:
            username = username.lower()
        return username


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        # Разрешаем менять только эти поля
        fields = [
            "first_name",
            "last_name",
            "middle_name",
            "phone",
            "vacation_start",
            "vacation_end",
        ]

        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ваше имя"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ваша фамилия"}
            ),
            "middle_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ваше отчество"}
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Например: 375291234567"}
            ),
            "vacation_start": forms.DateInput(
                format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}
            ),
            "vacation_end": forms.DateInput(
                format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}
            ),
        }
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "middle_name": "Отчество",
            "phone": "Мобильный телефон",
        }

    def clean_phone(self):
        """
        Дополнительная очистка телефона, если нужно.
        Например, можно запретить менять телефон на уже занятый другим юзером (хотя unique=True в модели это и так сделает).
        """
        phone = self.cleaned_data.get("phone")
        if not phone:
            return None
        # Можно тут вызвать наш format_phone_number, чтобы сохранять в едином формате,
        # но пока оставим как есть, пусть валидатор модели работает.
        return phone

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("vacation_start")
        end = cleaned_data.get("vacation_end")

        if start or end:
            # Валидация 1: Указана только одна дата
            if not start:
                # Ошибка появится прямо под полем "Начало (С):"
                self.add_error("vacation_start", "Укажите начало отпуска.")
            if not end:
                # Ошибка появится прямо под полем "Окончание (По):"
                self.add_error("vacation_end", "Укажите конец отпуска.")

            # Если обе даты заполнены, проверяем логику
            if start and end:
                # Валидация 2: Дата конца раньше даты начала
                if start > end:
                    self.add_error(
                        "vacation_end",
                        "Дата окончания не может быть раньше даты начала.",
                    )

                # Валидация 3: Максимальное количество дней (30)
                days_count = (end - start).days + 1
                if days_count > 30:
                    self.add_error(
                        "vacation_end",
                        f"Отпуск не может превышать 30 дней. Вы выбрали {days_count} дн.",
                    )

        return cleaned_data
