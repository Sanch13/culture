from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from users.models import User, UserAbsence


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "email",
        "first_name",
        "last_name",
        "role",
        "phone",
        "is_staff",
        "can_perform_inspections",
    )
    list_filter = (
        "role",
        "is_staff",
        "can_perform_inspections",
    )
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Личные данные", {"fields": ("first_name", "last_name", "phone", "role")}),
        # --- НОВАЯ СЕКЦИЯ ---
        (
            "Квалификация",
            {
                "fields": ("can_perform_inspections",),
                "description": "Настройки допуска к проведению проверок.",
            },
        ),
        # --------------------
        ("Права доступа", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password",
                    "first_name",
                    "last_name",
                    "role",
                    "can_perform_inspections",
                ),
            },
        ),
    )


@admin.register(UserAbsence)
class UserAbsenceAdmin(admin.ModelAdmin):
    list_display = ("user", "start_date", "end_date", "reason")
    list_filter = ("reason", "start_date")
    search_fields = ("user__last_name", "user__email")
    autocomplete_fields = ["user"]
