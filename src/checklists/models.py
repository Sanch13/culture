from django.db import models
from django.conf import settings
from django.utils import timezone

from django.contrib.auth import get_user_model

User = get_user_model()


# ==========================================
# БЛОК 1: КОНФИГУРАЦИЯ (Справочники)
# ==========================================


class Location(models.Model):
    """
    Производственный участок (например, 'Цех розлива').
    """

    name = models.CharField("Название участка", max_length=300)

    # 1. Начальник: показываем ТОЛЬКО тех, у кого роль 'manager'
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_locations",
        verbose_name="Начальник участка",
        limit_choices_to={"role": "manager"},  # <--- ФИЛЬТР
    )

    # Заместители (МНОГО)
    deputies = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="deputy_locations",
        verbose_name="Заместители начальника",
        limit_choices_to={"role": "deputy"},  # Фильтр в админке
    )

    # Старшие мастера (МНОГО)
    senior_masters = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="senior_master_locations",
        verbose_name="Старший мастер",
        limit_choices_to={"role": "senior_master"},  # Фильтр в админке
    )

    # 2. Мастера: показываем ТОЛЬКО тех, у кого роль 'master'
    masters = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="mastered_locations",
        verbose_name="Мастера участка",
        limit_choices_to={"role": "master"},  # <--- ФИЛЬТР
    )

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

    TYPE_A = "A"
    TYPE_B1 = "B1"
    TYPE_B2 = "B2"
    TYPE_C = "C"
    TYPE_D = "D"
    TYPE_GENERAL = "GENERAL"

    SECTION_TYPE_CHOICES = [
        (TYPE_A, "Раздел А (Рабочее пространство)"),
        (TYPE_B1, "Раздел В1 (ЭМО в цехах)"),
        (TYPE_B2, "Раздел В2 (Оборудование)"),
        (TYPE_C, "Раздел С (Участок в целом)"),
        (TYPE_D, "Раздел D (ЭМО)"),
        (TYPE_GENERAL, "Общий/Другой"),
    ]

    section_type = models.CharField(
        "Тип раздела (для расчетов)",
        max_length=10,
        choices=SECTION_TYPE_CHOICES,
        default=TYPE_GENERAL,
    )

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


class InspectionRoute(models.Model):
    """
    Маршрут обхода. Группирует несколько шаблонов, которые проверяются одним человеком за раз.
    Например: "Маршрут ОРП УПП" (включает Раздув и ЦПМ).
    """

    title = models.CharField("Название маршрута", max_length=200)

    # Связь Многие-ко-Многим: Один маршрут -> Много шаблонов
    templates = models.ManyToManyField(
        ChecklistTemplate, related_name="routes", verbose_name="Входящие шаблоны"
    )

    # Порядок для очереди генерации (какой маршрут первым, какой вторым)
    order = models.PositiveIntegerField("Порядок очереди", default=0)

    def __str__(self):
        return f"{self.title} ({self.templates.count()} шт.)"

    class Meta:
        ordering = ["order"]
        verbose_name = "Маршрут обхода"
        verbose_name_plural = "Справочник: Маршруты"


class InspectorRouteStat(models.Model):
    """
    Таблица-счетчик. Хранит информацию о том, сколько раз инспектор
    был назначен на конкретный маршрут.
    """

    inspector = models.ForeignKey(
        to=User,
        on_delete=models.CASCADE,
        related_name="route_stats",
        verbose_name="Проверяющий",
    )
    route = models.ForeignKey(
        to="InspectionRoute", on_delete=models.CASCADE, verbose_name="Маршрут"
    )
    visits_count = models.PositiveIntegerField("Количество назначений", default=0)

    class Meta:
        unique_together = ("inspector", "route")
        verbose_name = "Статистика инспектора по маршруту"
        verbose_name_plural = "Статистика по маршрутам"

    def __str__(self):
        return f"{self.inspector.last_name} -> {self.route.title}: {self.visits_count}"


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
    completed_at = models.DateTimeField(
        "Дата и время сдачи отчета", null=True, blank=True
    )

    # Snapshot: фиксируем название участка текстом на момент проверки
    location_snapshot = models.CharField("Участок (архив)", max_length=300)

    # Статус отчета (опционально, на будущее)
    is_completed = models.BooleanField("Проверка завершена", default=False)
    final_score = models.FloatField("Итоговый балл", null=True, blank=True)

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
    section_type = models.CharField(
        "Тип раздела (архив)", max_length=10, default="GENERAL"
    )
    is_repeated_violation = models.BooleanField("Повторное нарушение", default=False)
    consecutive_violations = models.PositiveIntegerField("Нарушений подряд", default=0)

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


class InspectionSectionScore(models.Model):
    """
    Балл за конкретный РАЗДЕЛ (A, B1, C...) в рамках одного отчета.
    Позволяет анализировать динамику конкретных проблем (например, чистоты).
    """

    inspection = models.ForeignKey(
        Inspection, on_delete=models.CASCADE, related_name="section_scores"
    )
    date_check = models.DateField("Дата проверки", db_index=True)
    section_name = models.CharField("Название раздела", max_length=300)
    section_type = models.CharField("Тип раздела", max_length=20)
    score = models.FloatField("Балл за раздел")

    def __str__(self):
        return f"{self.date_check} | {self.section_type}: {self.score}"

    class Meta:
        # Один раздел одного типа в одном отчете встречается один раз
        unique_together = ["inspection", "section_type"]
        ordering = ["-date_check", "section_type"]
        verbose_name = "Балл за раздел"
        verbose_name_plural = "Аналитика: Баллы за разделы"


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

    # ТИПЫ ПРИЧИН
    REASON_VACATION = "vacation"
    REASON_TRIP = "trip"
    REASON_SICK = "sick"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_VACATION, "Трудовой отпуск"),
        (REASON_TRIP, "Командировка"),
        (REASON_SICK, "Больничный лист"),
        (REASON_OTHER, "Другая причина"),
    ]
    # НОВОЕ ПОЛЕ: Тип причины
    reason_type = models.CharField(
        "Тип причины", max_length=20, choices=REASON_CHOICES, default=REASON_OTHER
    )

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
    reason = models.TextField("Причина замены", blank=True, null=True)

    class Meta:
        verbose_name = "История замен"
        verbose_name_plural = "Журнал: Замены"
        ordering = ["-created_at"]


class LocationDailyScore(models.Model):
    """
    Сводный итоговый балл УЧАСТКА за день.
    (Например, среднее по 4 отчетам УПП или сложный расчет для ЭМО).
    """

    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, verbose_name="Участок"
    )
    date = models.DateField("Дата", db_index=True)
    score = models.FloatField("Итоговый балл за день", null=True, blank=True)
    calculation_details = models.TextField("Детали расчета", blank=True)

    def __str__(self):
        return f"{self.location.name} | {self.date} | {self.score}"

    class Meta:
        unique_together = ["location", "date"]
        ordering = ["-date", "location"]
        verbose_name = "Сводный балл участка"
        verbose_name_plural = "Аналитика: Сводные баллы участков"


class CalendarOverride(models.Model):
    """
    Таблица для ручной настройки переносов рабочих/выходных дней (Постановления Совмина).
    Переопределяет стандартную логику календаря и библиотеки holidays.
    """

    date = models.DateField("Дата", unique=True, db_index=True)

    TYPE_WORKDAY = "work"
    TYPE_DAY_OFF = "off"

    DAY_TYPES = [
        (TYPE_WORKDAY, "Рабочий день"),
        (TYPE_DAY_OFF, "Доп. выходной"),
    ]

    day_type = models.CharField("Тип дня", max_length=10, choices=DAY_TYPES)
    description = models.CharField("Описание (для админа)", max_length=200, blank=True)

    def __str__(self):
        return f"{self.date}: {self.get_day_type_display()}"

    class Meta:
        ordering = ["-date"]
        verbose_name = "Исключение календаря (Перенос)"
        verbose_name_plural = "Справочник: Календарь (Переносы)"


class ScheduleGeneratorState(models.Model):
    """
    Хранит ID пользователя, на котором остановился генератор.
    Гарантирует стабильную очередь, независимую от обмена сменами.
    """

    last_user_id = models.IntegerField("ID последнего сотрудника", default=1)

    class Meta:
        verbose_name = "Состояние генератора"
        verbose_name_plural = "Состояние генератора"
