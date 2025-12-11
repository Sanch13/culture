from django.contrib import admin
from django.db import models
from django.forms import TextInput, Textarea
from checklists.models import (
    Location,
    ChecklistTemplate,
    ChecklistSection,
    ChecklistCriteria,
    Inspection,
    InspectionItem,
    ViolationPhoto,
    Schedule,
)


# --- Настройка справочников ---


class ChecklistCriteriaInline(admin.TabularInline):
    """Позволяет добавлять вопросы сразу внутри Раздела"""

    model = ChecklistCriteria
    extra = 1

    formfield_overrides = {
        models.CharField: {
            "widget": Textarea(attrs={"rows": 5, "cols": 80, "style": "width: 90%;"})
        },
    }


@admin.register(ChecklistSection)
class ChecklistSectionAdmin(admin.ModelAdmin):
    list_display = ("title", "template", "order")
    list_filter = ("template",)
    inlines = [ChecklistCriteriaInline]  # <-- Вставляем вопросы сюда
    search_fields = ("title", "template__name")

    formfield_overrides = {
        models.CharField: {
            "widget": TextInput(attrs={"size": "100"})  # size=100 делает строку длинной
        },
    }


class ChecklistSectionInline(admin.TabularInline):
    """Позволяет видеть разделы внутри Шаблона (без вопросов)"""

    model = ChecklistSection
    extra = 0
    show_change_link = (
        True  # Кнопка "Редактировать", чтобы провалиться в раздел и добавить вопросы
    )

    formfield_overrides = {
        models.CharField: {"widget": TextInput(attrs={"size": "80"})},
    }


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "location")
    list_filter = ("location",)
    inlines = [ChecklistSectionInline]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    pass


# --- Настройка Журнала проверок ---


class ViolationPhotoInline(admin.TabularInline):
    model = ViolationPhoto
    extra = 0


class InspectionItemInline(admin.StackedInline):
    """Показываем пункты проверки внутри самого Отчета"""

    model = InspectionItem
    extra = 0
    # Делаем поля readonly, чтобы случайно не поменять архивные данные через админку
    readonly_fields = ("section_name", "criteria_text", "criteria_order")
    can_delete = False

    # Фотографии обычно привязываются к Item, в админке это сложно отобразить вложенно.
    # Поэтому фото лучше смотреть через отдельную админку Item или кастомный view.


@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "date_check",
        "location_snapshot",
        "inspector",
        "is_completed",
    )
    list_filter = ("date_check", "inspector", "template")
    inlines = [InspectionItemInline]

    # Поля, которые нельзя менять после создания (защита истории)
    readonly_fields = ("created_at", "location_snapshot")


@admin.register(InspectionItem)
class InspectionItemAdmin(admin.ModelAdmin):
    """Отдельный просмотр конкретных ответов (удобно для поиска нарушений)"""

    list_display = ("inspection", "section_name", "criteria_text", "is_compliant")
    list_filter = ("is_compliant", "inspection__date_check")
    search_fields = ("comment", "criteria_text")
    inlines = [ViolationPhotoInline]  # <-- Здесь можно добавлять фото


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("date", "template", "inspector", "status_display")
    list_filter = ("date", "template", "inspector")
    date_hierarchy = "date"  # Удобная навигация по датам сверху

    def status_display(self, obj):
        if obj.inspection:
            return "✅ Выполнено"
        return "⚪️ Ожидает"

    status_display.short_description = "Статус"
