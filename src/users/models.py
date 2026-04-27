from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator

from users.managers import CustomUserManager
from users.validators import cyrillic_regex


class User(AbstractUser):
    username = None
    first_name = models.CharField(
        verbose_name="Имя", max_length=100, blank=False, validators=[cyrillic_regex]
    )
    last_name = models.CharField(
        verbose_name="Фамилия", max_length=100, blank=False, validators=[cyrillic_regex]
    )
    middle_name = models.CharField(
        verbose_name="Отчество", max_length=100, blank=True, validators=[cyrillic_regex]
    )
    email = models.EmailField("Email", unique=True)

    # --- ОБНОВЛЯЕМ ПОЛЕ ТЕЛЕФОН ---
    # Создаем правило: "Начинается с +, дальше от 9 до 15 цифр"
    phone_regex = RegexValidator(
        regex=r"^\d{9,15}$",
        message="Телефон должен состоять только из цифр (от 9 до 15). Пример: 375445895647",
    )

    phone = models.CharField(
        "Телефон",
        validators=[phone_regex],
        max_length=20,
        unique=True,
        blank=True,
        null=True,
    )
    can_perform_inspections = models.BooleanField(
        "Может проводить проверки",
        default=False,
        help_text="Отметьте, если этот сотрудник должен участвовать в расписании.",
    )

    ROLE_WORKER = "worker"
    ROLE_MANAGER = "manager"
    ROLE_DEPUTY = "deputy"
    ROLE_SENIOR_MASTER = "senior_master"
    ROLE_MASTER = "master"

    ROLE_PRODUCTION_CHIEF = "production_chief"
    ROLE_OBSERVER = "observer"
    ROLE_ADMIN = "admin"

    ROLE_CHOICES = [
        (ROLE_WORKER, "Сотрудник"),
        (ROLE_MANAGER, "Начальник участка"),
        (ROLE_DEPUTY, "Заместитель начальника"),
        (ROLE_SENIOR_MASTER, "Старший мастер"),
        (ROLE_MASTER, "Мастер участка"),
        (ROLE_PRODUCTION_CHIEF, "Начальник производства"),
        (ROLE_OBSERVER, "Наблюдатель (Видит всё)"),
        (ROLE_ADMIN, "Администратор"),
    ]

    role = models.CharField(
        "Роль", max_length=20, choices=ROLE_CHOICES, default=ROLE_WORKER
    )

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"

    def get_full_name(self):
        """
        Переопределяем стандартный метод Django.
        Теперь он возвращает Фамилия Имя Отчество (если есть).
        """
        parts = [self.last_name, self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        return " ".join(parts).strip()

    def get_short_name(self):
        """
        Возвращает Инициалы (Иванов И. И.)
        """
        name = f"{self.last_name}"
        if self.first_name:
            name += f" {self.first_name[0]}."
        if self.middle_name:
            name += f"{self.middle_name[0]}."
        return name

    def __str__(self):
        return f"[{self.id}] {self.get_full_name()} <{self.email}>"


class UserAbsence(models.Model):
    """
    Учет отсутствий (Отпуск, Больничный, Отгул).
    Чтобы исключать сотрудника из расписания на этот период.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="absences",
        verbose_name="Сотрудник",
    )
    start_date = models.DateField("Начало")
    end_date = models.DateField("Конец")

    REASON_SICK = "sick"
    REASON_VACATION = "vacation"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_SICK, "Болезнь"),
        (REASON_VACATION, "Отпуск"),
        (REASON_OTHER, "Другое"),
    ]
    reason = models.CharField(
        "Причина", max_length=20, choices=REASON_CHOICES, default=REASON_VACATION
    )
    comment = models.CharField("Комментарий", max_length=200, blank=True)

    def __str__(self):
        return f"{self.user.last_name}: {self.start_date} - {self.end_date} ({self.get_reason_display()})"

    class Meta:
        verbose_name = "Отсутствие сотрудника"
        verbose_name_plural = "График отсутствий"


class AllowedCorporateEmail(models.Model):
    """
    Белый список корпоративных email-адресов.
    Заполняется автоматически из Mailcow или вручную администратором.
    """

    email = models.EmailField("Разрешенный Email", unique=True)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

    class Meta:
        verbose_name = "Разрешенный Email"
        verbose_name_plural = "Белый список Email"
