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
from checklists.utils import format_phone_number

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
        current_user = schedule_item.inspector
        target_user = candidate.inspector

        current_date = schedule_item.date
        target_date = candidate.date

        # Меняем владельцев
        schedule_item.inspector = target_user
        candidate.inspector = current_user

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
            requestor=current_user,
            target_user=target_user,
            source_date=current_date,
            target_date=target_date,
            reason=reason,
        )

    return (
        True,
        f"Обмен выполнен. Вы перенесены на {target_date}. Вместо вас выйдет {target_user.last_name}.",
    )


def _get_production_chief_text():
    """
    Возвращает готовый текст с контактами Начальника производства.
    Использует кэширование запроса (если нужно), но пока просто берет из БД.
    """
    chief = User.objects.filter(role=User.ROLE_PRODUCTION_CHIEF, is_active=True).first()

    if not chief:
        return ""

    phone = format_phone_number(chief.phone) or "нет телефона"
    return (
        f"\n------------------------\n"
        f"❗️ В случае отсутствия кого-то из руководителей производственного участка "
        f"обращаться к Начальнику производства:\n"
        f"{chief.first_name} {chief.last_name} (Тел: {phone})"
    )


def _get_location_contacts_text(location):
    """
    Собирает контакты участка (Начальник, Зам, Ст. мастер, Мастера).
    """
    lines = []

    # 1. Руководство
    if location.manager:
        p = format_phone_number(location.manager.phone)
        lines.append(f"👤 Начальник участка: {location.manager.get_full_name()} ({p})")

    if location.deputy:
        p = format_phone_number(location.deputy.phone)
        lines.append(f"👤 Зам. начальника: {location.deputy.get_full_name()} ({p})")

    if location.senior_master:
        p = format_phone_number(location.senior_master.phone)
        lines.append(
            f"👷‍♂️ Старший мастер: {location.senior_master.get_full_name()} ({p})"
        )

    # 2. Мастера
    masters = location.masters.all()
    if masters:
        lines.append("👷 Мастера:")
        for m in masters:
            p = format_phone_number(m.phone)
            lines.append(f"   - {m.get_full_name()} ({p})")

    if not lines:
        return "Локальные контакты не назначены."

    return "\n".join(lines)


def build_inspection_email_body(schedule_item, intro_message):
    """
    Генерирует полный текст письма.
    :param schedule_item: объект Schedule
    :param intro_message: Уникальное вступление (строка)
    """
    inspector = schedule_item.inspector
    location = schedule_item.template.location
    date_str = schedule_item.date.strftime("%d.%m.%Y")

    # Получаем куски текста
    contacts_text = _get_location_contacts_text(location)
    chief_text = _get_production_chief_text()

    # Собираем конструктор
    return (
        f"Здравствуйте, {inspector.first_name}!\n\n"
        f"{intro_message}\n"  # <--- ВСТАВЛЯЕМ УНИКАЛЬНОЕ ВСТУПЛЕНИЕ
        f"📅 Дата: {date_str}\n"
        f"📍 Участок: {location.name}\n"
        f"📋 Чек-лист: {schedule_item.template.name}\n\n"
        f"--- КОНТАКТЫ ДЛЯ СВЯЗИ ---\n"
        f"{contacts_text}\n"
        f"{chief_text}\n\n"
        f"------------------------\n"
        f"Пожалуйста, не забудьте заполнить отчет в системе."
    )


def get_swap_notification_data(schedule_id):
    """
    Данные для уведомления о ЗАМЕНЕ.
    """
    try:
        item = (
            Schedule.objects.select_related(
                "inspector",
                "template",
                "template__location",
                "template__location__manager",
                "template__location__deputy",
                "template__location__senior_master",
            )
            .prefetch_related("template__location__masters")
            .get(id=schedule_id)
        )
    except Schedule.DoesNotExist:
        return None

    if not item.inspector.email:
        return None

    # Уникальное сообщение для ЗАМЕНЫ
    intro = "⚠️ ВНИМАНИЕ: Вам назначена новая проверка (в порядке замены)."

    # Вызываем строитель
    body = build_inspection_email_body(item, intro)

    return {
        "email": item.inspector.email,
        "subject": f"⚡ Назначение замены: {item.template.location.name}",
        "body": body,
    }


def prepare_daily_notifications(target_date=None):
    """
    Данные для ЕЖЕДНЕВНОЙ рассылки.
    """
    if target_date is None:
        target_date = datetime.date.today()

    # 1. ЗАГРУЖАЕМ РАСПИСАНИЕ
    schedules = (
        Schedule.objects.filter(date=target_date)
        .select_related(
            "inspector",
            "template",
            "template__location",
            "template__location__manager",
            "template__location__deputy",
            "template__location__senior_master",
        )
        .prefetch_related("template__location__masters")
    )

    notifications_data = []
    for item in schedules:
        if not item.inspector.email:
            continue

        intro = "Напоминаем, что у вас запланирована плановая проверка."

        body = build_inspection_email_body(item, intro)

        notifications_data.append(
            {
                "recipient_email": item.inspector.email,
                "subject": f"Напоминание о проверке: {item.template.location.name}",
                "body": body,
            }
        )

    # Подмена для теста ПОТОМ удалить !!!
    for item in notifications_data:
        item["recipient_email"] = "a.zubchyk@miran-bel.com"
    # Подмена для теста ПОТОМ удалить !!!

    return notifications_data
