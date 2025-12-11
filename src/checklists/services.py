import datetime
import holidays

from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from checklists.models import (
    Inspection,
    InspectionItem,
    Schedule,
    ChecklistTemplate,
    SwapLog,
)

User = get_user_model()


def generate_schedule(start_date, days_count=7):
    """
    Генерирует расписание.
    Алгоритм: Round Robin (Круговая очередь) с памятью в БД.
    """
    # 1. Загружаем праздники Беларуси
    by_holidays = holidays.BY()

    # 2. Получаем ресурсы
    # Шаблоны сортируем по ID, чтобы порядок всегда был одинаковый
    templates = list(ChecklistTemplate.objects.all().order_by("id"))

    # Инспекторы: только активные и с допуском. Сортируем по ID (стабильность списка)
    inspectors = list(
        User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id")
    )

    if not templates:
        return "Ошибка: Нет шаблонов (ChecklistTemplate)."
    if not inspectors:
        return "Ошибка: Нет сотрудников (User с can_perform_inspections=True)."

    # 3. ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА ОЧЕРЕДИ
    # Смотрим, кто был последним назначенным в расписании ВООБЩЕ
    last_entry = Schedule.objects.order_by("-date", "-id").first()

    start_index = 0
    if last_entry:
        try:
            # Находим, каким по счету в нашем списке стоит этот сотрудник
            last_inspector_index = inspectors.index(last_entry.inspector)
            # Следующим будет (index + 1)
            start_index = (last_inspector_index + 1) % len(inspectors)
        except ValueError:
            # Если сотрудник был уволен и его нет в списке inspectors -> начинаем с 0
            start_index = 0

    # Текущий указатель (кто сейчас дежурит)
    current_inspector_idx = start_index

    # 4. ГЕНЕРАЦИЯ ПО ДНЯМ
    created_total = 0
    current_date = start_date

    # transaction.atomic гарантирует: либо создадим всё, либо (при ошибке) ничего.
    with transaction.atomic():
        for _ in range(days_count):
            # А. Проверка на Выходные (Saturday=5, Sunday=6)
            if current_date.weekday() >= 5:
                # print(f"Пропуск: {current_date} (Выходной)")
                current_date += datetime.timedelta(days=1)
                continue

            # Б. Проверка на Праздники
            if current_date in by_holidays:
                # print(f"Пропуск: {current_date} (Праздник: {by_holidays.get(current_date)})")
                current_date += datetime.timedelta(days=1)
                continue

            # В. Назначение проверок
            # Для каждого шаблона (Цеха) берем СЛЕДУЮЩЕГО сотрудника
            for template in templates:
                inspector = inspectors[current_inspector_idx]

                # Проверяем, не создано ли уже расписание на этот день (защита от дублей)
                if not Schedule.objects.filter(
                    date=current_date, template=template
                ).exists():
                    Schedule.objects.create(
                        date=current_date, template=template, inspector=inspector
                    )
                    created_total += 1

                    # Сдвигаем очередь! Следующий цех проверяет следующий человек.
                    # Это обеспечивает равномерную нагрузку.
                    current_inspector_idx = (current_inspector_idx + 1) % len(
                        inspectors
                    )

            # Переходим к следующему дню
            current_date += datetime.timedelta(days=1)

    return f"Генерация завершена. Создано записей: {created_total}."


def create_inspection_from_template(template, user, date, location_snapshot):
    """
    Бизнес-логика: Создание экземпляра проверки на основе шаблона.
    Здесь происходит копирование вопросов (Snapshot).
    """

    # transaction.atomic() гарантирует: либо создастся всё целиком,
    # либо (если произойдет ошибка) не создастся ничего. Не будет "половинчатых" отчетов.
    with transaction.atomic():
        # 1. Создаем шапку отчета
        inspection = Inspection.objects.create(
            template=template,
            inspector=user,
            date_check=date,
            location_snapshot=location_snapshot,
        )

        # 2. Получаем все разделы шаблона (упорядоченные по order)
        sections = template.sections.all().order_by("order")

        # 3. Итерируемся по разделам
        for section in sections:
            # Получаем вопросы внутри раздела
            criteria_list = section.criteria.all().order_by("order")

            for criteria in criteria_list:
                # 4. Создаем строку отчета (Snapshot)
                InspectionItem.objects.create(
                    inspection=inspection,
                    criteria_origin=criteria,  # Ссылка на родителя (если нужно для аналитики)
                    # КОПИРУЕМ ДАННЫЕ (фиксируем историю)
                    section_name=section.title,
                    criteria_text=criteria.text,
                    criteria_order=criteria.order,
                    # Значение по умолчанию
                    is_compliant=True,
                )

        return inspection


def perform_auto_swap(schedule_item, reason):
    """
    Меняет смены местами.
    Аргументы:
    - schedule_item: Задание, от которого хотят отказаться.
    - reason: Текст причины.
    """

    # 1. Вычисляем дату начала СЛЕДУЮЩЕЙ недели (Понедельник)
    today = timezone.now().date()
    # today.weekday(): 0=Пн ... 6=Вс
    days_until_next_monday = 7 - today.weekday()
    if days_until_next_monday <= 0:  # Защита, хотя 7-x всегда > 0
        days_until_next_monday = 7

    start_of_next_week = today + datetime.timedelta(days=days_until_next_monday)

    # 2. Ищем кандидата
    # Условия:
    # - Дата >= Понедельник следующей недели
    # - Инспектор НЕ я
    # - Отчет еще не начат
    # - is_swapped = False (ГЛАВНОЕ: Ищем только "чистые" слоты, тех, кто еще не менялся)

    candidate = (
        Schedule.objects.filter(
            date__gte=start_of_next_week,
            inspection__isnull=True,
            is_swapped=False,  # <--- ЗАЩИТА ОТ ПИНГ-ПОНГА
        )
        .exclude(inspector=schedule_item.inspector)
        .order_by("date", "id")
        .first()
    )

    if not candidate:
        # Если "чистых" кандидатов нет, пробуем искать любых (крайний случай),
        # но лучше просто вернуть ошибку, чтобы админ расширил расписание.
        return (
            False,
            "Нет доступных кандидатов на следующей неделе. Попросите администратора сгенерировать расписание дальше.",
        )

    # 3. Совершаем обмен
    with transaction.atomic():
        initiator = schedule_item.inspector
        target_user = candidate.inspector

        old_date = schedule_item.date
        new_date = candidate.date

        # Меняем владельцев
        schedule_item.inspector = target_user
        candidate.inspector = initiator

        # Помечаем, что этот слот (в будущем) теперь "Грязный" (занят по обмену).
        # Теперь этого инициатора никто не сможет выдернуть оттуда автоматом.
        candidate.is_swapped = True

        # Слот "Сегодня" (куда попала жертва) мы НЕ помечаем is_swapped=True,
        # или помечаем?
        # Если пометим, то "Жертва" не сможет тоже нажать "Автозамена" (если мы фильтруем is_swapped=False).
        # Давай оставим False, чтобы "Жертва" тоже имела право отказаться, если у неё форс-мажор.
        schedule_item.is_swapped = False

        schedule_item.save()
        candidate.save()

        # 4. Пишем в Историю
        SwapLog.objects.create(
            requestor=initiator,
            target_user=target_user,
            source_date=old_date,
            target_date=new_date,
            reason=reason,
        )

    return (
        True,
        f"Обмен выполнен. Вы перенесены на {new_date}. Вместо вас выйдет {target_user.last_name}.",
    )
