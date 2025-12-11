from django.db import models
from django.conf import settings
from django.utils import timezone


# ==========================================
# БЛОК 1: КОНФИГУРАЦИЯ (Справочники)
# ==========================================


class Location(models.Model):
    """
    Производственный участок (например, 'Цех розлива').
    """

    name = models.CharField("Название участка", max_length=300)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Участок"
        verbose_name_plural = "Справочник: Участки"


class ChecklistTemplate(models.Model):
    """
    Шаблон чек-листа (например, 'Ежедневная проверка ЭМО').
    """

    name = models.CharField("Название шаблона", max_length=300)
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="templates",
        verbose_name="Участок",
    )

    def __str__(self):
        return f"{self.name} ({self.location.name})"

    class Meta:
        verbose_name = "Шаблон"
        verbose_name_plural = "Справочник: Шаблоны"


class ChecklistSection(models.Model):
    """
    Раздел внутри шаблона (например, 'А. Рабочее пространство').
    """

    template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name="Шаблон",
    )
    title = models.CharField("Заголовок раздела", max_length=300)
    order = models.PositiveIntegerField("Порядок сортировки", default=0)

    def __str__(self):
        return f"{self.title} (Шаблон: {self.template.name})"

    class Meta:
        ordering = ["order"]
        verbose_name = "Раздел"
        verbose_name_plural = "Справочник: Разделы"


class ChecklistCriteria(models.Model):
    """
    Вопрос/Критерий проверки.
    """

    section = models.ForeignKey(
        ChecklistSection,
        on_delete=models.CASCADE,
        related_name="criteria",
        verbose_name="Раздел",
    )
    text = models.CharField("Текст вопроса", max_length=1000)
    order = models.PositiveIntegerField("Порядок сортировки", default=0)

    def __str__(self):
        return self.text[:50]

    class Meta:
        ordering = ["order"]
        verbose_name = "Критерий"
        verbose_name_plural = "Справочник: Критерии"


# ==========================================
# БЛОК 2: ОПЕРАЦИОННЫЕ ДАННЫЕ (Отчеты)
# ==========================================


class Inspection(models.Model):
    """
    Шапка отчета о проверке.
    """

    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Проверяющий"
    )
    template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.PROTECT,
        verbose_name="Использованный шаблон",
    )
    date_check = models.DateField("Дата проверки", default=timezone.now)
    created_at = models.DateTimeField("Дата создания записи", auto_now_add=True)

    # Snapshot: фиксируем название участка текстом на момент проверки
    location_snapshot = models.CharField("Участок (архив)", max_length=300)

    # Статус отчета (опционально, на будущее)
    is_completed = models.BooleanField("Проверка завершена", default=False)

    def __str__(self):
        return f"Отчет от {self.date_check} - {self.location_snapshot}"

    class Meta:
        ordering = ["-date_check"]
        verbose_name = "Отчет о проверке"
        verbose_name_plural = "Журнал: Отчеты"

        # --- НОВОЕ ОГРАНИЧЕНИЕ ---
        # Уникальная пара: Шаблон + Дата
        unique_together = ["template", "date_check"]


class InspectionItem(models.Model):
    """
    Строка отчета (Ответ на конкретный вопрос).
    Хранит копию вопроса на момент создания отчета.
    """

    inspection = models.ForeignKey(
        Inspection, on_delete=models.CASCADE, related_name="items"
    )

    # Ссылка на оригинал (может быть null, если вопрос удалили из справочника)
    criteria_origin = models.ForeignKey(
        ChecklistCriteria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ссылка на оригинал",
    )

    # --- SNAPSHOT FIELDS (Копии данных) ---
    section_name = models.CharField("Раздел (архив)", max_length=300)
    criteria_text = models.CharField("Вопрос (архив)", max_length=1000)
    criteria_order = models.PositiveIntegerField("Порядок (архив)", default=0)

    # --- РЕЗУЛЬТАТЫ ---
    # True = 1 (Соответствует), False = 0 (Не соответствует)
    is_compliant = models.BooleanField(
        "Соответствие",
        choices=[(True, "Соответствует"), (False, "Не соответствует")],
        default=True,
    )
    comment = models.TextField("Комментарий", blank=True)

    def __str__(self):
        status = "✅" if self.is_compliant else "❌"
        return f"{status} {self.criteria_text[:30]}..."

    class Meta:
        # Сортируем так, как это было в шаблоне (по разделу, потом по порядку вопроса)
        ordering = ["inspection", "section_name", "criteria_order"]
        verbose_name = "Результат пункта"
        verbose_name_plural = "Результаты пунктов"


class ViolationPhoto(models.Model):
    """
    Фотографии нарушений.
    """

    item = models.ForeignKey(
        InspectionItem, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField("Фото", upload_to="violations/%Y/%m/%d/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото нарушения"
        verbose_name_plural = "Фото нарушений"


class Schedule(models.Model):
    """
    План-график проверок.
    Генерируется автоматически, но может быть изменен вручную (админом или автозаменой).
    """

    # Кто проверяет? (Вася)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedule_items",
        verbose_name="Назначенный сотрудник",
    )

    # Что проверяет? (Цех №1)
    template = models.ForeignKey(
        ChecklistTemplate, on_delete=models.CASCADE, verbose_name="Шаблон проверки"
    )

    # Когда? (2025-10-25)
    date = models.DateField("Дата назначения")

    # Результат (Ссылка на отчет)
    # Изначально пусто. Заполнится, когда Вася нажмет "Начать".
    inspection = models.OneToOneField(
        Inspection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedule_item",
        verbose_name="Выполненный отчет",
    )
    is_swapped = models.BooleanField("Была замена", default=False)

    def __str__(self):
        status = "✅" if self.inspection else "⚪️"
        return (
            f"{status} {self.date} | {self.template.name} -> {self.inspector.last_name}"
        )

    class Meta:
        # ЗАЩИТА: Нельзя запланировать две проверки одного шаблона на один день.
        # (Если у вас по бизнес-логике можно проверять один цех 2 раза в день — убери эту строку).
        unique_together = ["template", "date"]

        ordering = ["date", "template"]
        verbose_name = "Запись в расписании"
        verbose_name_plural = "График проверок"


class SwapLog(models.Model):
    """
    История замен (кто, когда и почему отказался).
    Нужна для администратора, чтобы видеть 'прогульщиков'.
    """

    # Кто запросил замену (Инициатор)
    requestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="swap_requests",
        verbose_name="Инициатор",
    )
    # С кем поменялся (Жертва)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="swap_targets",
        verbose_name="На кого заменили",
    )
    # Дата самого действия
    created_at = models.DateTimeField(auto_now_add=True)

    # Какую дату отдал
    source_date = models.DateField("Дата (была)")
    # Какую дату получил
    target_date = models.DateField("Дата (стала)")

    # Причина (обязательно)
    reason = models.TextField("Причина замены")

    class Meta:
        verbose_name = "История замен"
        verbose_name_plural = "Журнал: Замены"
        ordering = ["-created_at"]
