from django.contrib.auth.models import AbstractUser
from django.db import models

from users.managers import CustomUserManager


class User(AbstractUser):
    username = None
    first_name = models.CharField(verbose_name="Имя", max_length=100, blank=False)
    last_name = models.CharField(verbose_name="Фамилия", max_length=100, blank=False)
    email = models.EmailField("Email", unique=True)
    phone = models.CharField(
        "Телефон", max_length=20, unique=True, blank=True, null=True
    )
    can_perform_inspections = models.BooleanField(
        "Может проводить проверки",
        default=False,
        help_text="Отметьте, если этот сотрудник должен участвовать в расписании.",
    )

    ROLE_WORKER = "worker"
    ROLE_MASTER = "master"
    ROLE_ADMIN = "admin"

    ROLE_CHOICES = [
        (ROLE_WORKER, "Сотрудник"),
        (ROLE_MASTER, "Мастер участка"),
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

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}>"


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
