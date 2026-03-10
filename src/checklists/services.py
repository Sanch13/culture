import datetime
import holidays

from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.core.paginator import Paginator

from checklists.models import (
    Inspection,
    InspectionItem,
    Schedule,
    InspectionRoute,
    SwapLog,
    Location,
)
from checklists.utils import format_phone_number

User = get_user_model()


def generate_schedule(start_date, days_count=7):
    """
    Генерирует расписание на основе МАРШРУТОВ (InspectionRoute).
    """
    by_holidays = holidays.BY()

    # 1. Загружаем МАРШРУТЫ (а не шаблоны)
    # Сортируем по order, чтобы порядок раздачи был фиксирован
    # prefetch_related('templates') загружает связанные шаблоны сразу, чтобы не тормозить
    routes = list(
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )

    inspectors = list(
        User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id")
    )

    if not routes:
        return (
            "Ошибка: Не настроены Маршруты (InspectionRoute). Заполните их в админке."
        )
    if not inspectors:
        return "Ошибка: Нет сотрудников-инспекторов."

    # 2. ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА ОЧЕРЕДИ
    last_entry = Schedule.objects.order_by("-date", "-id").first()

    start_index = 0
    if last_entry:
        try:
            last_inspector_index = inspectors.index(last_entry.inspector)
            start_index = (last_inspector_index + 1) % len(inspectors)
        except ValueError:
            start_index = 0

    current_inspector_idx = start_index
    created_total = 0
    current_date = start_date

    with transaction.atomic():
        for _ in range(days_count):
            # Пропуск выходных/праздников
            if current_date.weekday() >= 5:
                current_date += datetime.timedelta(days=1)
                continue

            if current_date in by_holidays:
                current_date += datetime.timedelta(days=1)
                continue

            # 3. НАЗНАЧЕНИЕ ПО МАРШРУТАМ
            for route in routes:
                inspector = inspectors[current_inspector_idx]

                # Получаем список шаблонов внутри этого маршрута (например, Раздув + ЦПМ)
                templates_in_route = route.templates.all()

                if not templates_in_route:
                    # Пустой маршрут? Пропускаем, очередь не двигаем
                    continue

                # Флаг: удалось ли что-то назначить?
                assigned_something = False

                # Назначаем ЭТОМУ ЖЕ инспектору ВСЕ шаблоны из маршрута
                for tmpl in templates_in_route:
                    # Защита от дублей: если на этот день и шаблон уже есть запись -> пропускаем
                    if not Schedule.objects.filter(
                        date=current_date, template=tmpl
                    ).exists():
                        Schedule.objects.create(
                            date=current_date, template=tmpl, inspector=inspector
                        )
                        created_total += 1
                        assigned_something = True

                # Сдвигаем очередь ТОЛЬКО если мы реально назначили работу
                if assigned_something:
                    current_inspector_idx = (current_inspector_idx + 1) % len(
                        inspectors
                    )

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
    Групповая автозамена.
    Меняет ВСЕ смены инициатора на эту дату на ВСЕ смены жертвы на будущую дату.
    """
    current_date = schedule_item.date
    current_user = schedule_item.inspector

    # 1. Находим ВСЕ задачи инициатора на этот день (чтобы отдать их все)
    current_tasks = list(
        Schedule.objects.filter(date=current_date, inspector=current_user)
    )

    # Защита: вдруг хоть один из отчетов уже начат? (Хотя view это уже проверила)
    for t in current_tasks:
        if t.inspection:
            return (
                False,
                "Нельзя обменять смену, так как часть проверок уже начата.",
                "",
            )

    # 2. Вычисляем дату начала следующей недели
    today = timezone.now().date()
    days_until_next_monday = 7 - today.weekday()
    if days_until_next_monday <= 0:
        days_until_next_monday = 7
    start_of_next_week = today + datetime.timedelta(days=days_until_next_monday)

    # 3. Ищем кандидата-жертву (любую неначатую смену на следующей неделе)
    candidate_task = (
        Schedule.objects.filter(
            date__gte=start_of_next_week,
            inspection__isnull=True,
            is_swapped=False,  # Только тех, кто еще не менялся
        )
        .exclude(inspector=current_user)
        .order_by("date", "id")
        .first()
    )

    if not candidate_task:
        return (
            False,
            "Нет доступных кандидатов на следующей неделе. Сообщите администратору.",
            "",
        )

    target_date = candidate_task.date
    target_user = candidate_task.inspector

    # 4. Находим ВСЕ задачи жертвы на тот день (чтобы забрать их все)
    target_tasks = list(
        Schedule.objects.filter(date=target_date, inspector=target_user)
    )

    # 5. СОВЕРШАЕМ ОБМЕН
    with transaction.atomic():
        # Мои задачи отдаем ему
        for t in current_tasks:
            t.inspector = target_user
            t.is_swapped = False  # Оставляем False, чтобы жертва могла отказаться
            t.save()

        # Его задачи забираю я
        for t in target_tasks:
            t.inspector = current_user
            t.is_swapped = True  # Я переехал, меня больше трогать нельзя
            t.save()

        # 6. Пишем лог (ОДИН лог на весь обмен, не нужно плодить дубли)
        SwapLog.objects.create(
            requestor=current_user,
            target_user=target_user,
            source_date=current_date,
            target_date=target_date,
            reason=reason,
        )

    info_about_change = (
        f"Дата: {current_date}.\nПроверяющий {current_user.last_name} {current_user.first_name} "
        f"заменился на {target_user.last_name} {target_user.first_name}.\nПричина: {reason}"
    )

    return (
        True,
        f"Обмен выполнен. Вы перенесены на {target_date.strftime('%d.%m')}. Вместо вас выйдет {target_user.last_name}.",
        info_about_change,
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
    Собирает контакты участка (Начальник, Замы, Ст. мастера, Мастера).
    """
    lines = []

    # 1. Начальник участка (Один)
    if location.manager:
        p = format_phone_number(location.manager.phone)
        lines.append(f"👤 Начальник участка: {location.manager.get_full_name()} ({p})")

    # 2. Заместители (Много)
    # Используем .all() так как теперь это ManyToMany
    deputies = location.deputies.all()
    if deputies:
        # Если зам один - пишем красиво в одну строку (опционально), но проще списком
        lines.append("👤 Заместители начальника:")
        for dep in deputies:
            p = format_phone_number(dep.phone)
            lines.append(f"   - {dep.get_full_name()} ({p})")

    # 3. Старшие мастера (Много)
    senior_masters = location.senior_masters.all()
    if senior_masters:
        lines.append("👷‍♂️ Старшие мастера:")
        for sm in senior_masters:
            p = format_phone_number(sm.phone)
            lines.append(f"   - {sm.get_full_name()} ({p})")

    # 4. Мастера (Много)
    masters = location.masters.all()
    if masters:
        lines.append("👷 Мастера:")
        for m in masters:
            p = format_phone_number(m.phone)
            lines.append(f"   - {m.get_full_name()} ({p})")

    if not lines:
        return "Локальные контакты не назначены."

    return "\n".join(lines)


def build_composite_email_body(schedules, intro_message):
    """
    Генерирует полный текст письма для нескольких шаблонов и участков.
    """
    inspector = schedules[0].inspector
    date_str = schedules[0].date.strftime("%d.%m.%Y")

    # 1. Шапка
    body = [
        f"Здравствуйте, {inspector.first_name}!\n",
        f"{intro_message}\n",
        f"📅 Дата смены: {date_str}\n",
    ]

    # 2. Собираем уникальные участки и список шаблонов
    unique_locations = set()
    template_names = []

    for item in schedules:
        location = item.template.location
        unique_locations.add(location)
        template_names.append(f" - {item.template.name} (Участок: {location.name})")

    body.append("📋 Вам назначены следующие проверки:")
    body.extend(template_names)
    body.append("\n--- КОНТАКТЫ ДЛЯ СВЯЗИ ---")

    # 3. Выводим контакты только для уникальных участков
    for location in unique_locations:
        body.append(f"\n🏢 {location.name.upper()}")
        body.append(_get_location_contacts_text(location))

    # 4. Добавляем главного босса (один раз в конце)
    body.append(_get_production_chief_text())

    body.append("\n------------------------")
    body.append("Пожалуйста, не забудьте заполнить отчеты в системе.")

    return "\n".join(body)


def get_swap_notification_data(date_str, user_id):
    """
    Собирает данные для письма на основе ВСЕХ задач инспектора на конкретный день.
    """
    # 1. Загружаем все задачи (расписание) человека на этот день
    schedules = list(
        Schedule.objects.filter(date=date_str, inspector_id=user_id)
        .select_related(
            "inspector",
            "template",
            "template__location",
            "template__location__manager",
        )
        .prefetch_related(
            "template__location__masters",
            "template__location__deputies",
            "template__location__senior_masters",
        )
    )

    if not schedules:
        return None

    inspector = schedules[0].inspector
    if not inspector.email:
        return None

    # Уникальное сообщение для ЗАМЕНЫ
    intro = "⚠️ ВНИМАНИЕ: Вам назначена новая смена (в порядке замены)."

    # 2. Вызываем строитель, передавая СПИСОК расписаний
    body = build_composite_email_body(schedules, intro)

    return {
        "email": inspector.email,
        "subject": f"⚡ Назначение смены: {date_str}",
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
        )
        .prefetch_related(
            "template__location__masters",
            "template__location__deputies",  # <--- ДОБАВИЛИ СЮДА
            "template__location__senior_masters",  # <--- ДОБАВИЛИ СЮДА
        )
    )

    notifications_data = []
    for item in schedules:
        if not item.inspector.email:
            continue

        intro = "Напоминаем, что у вас запланирована плановая проверка."

        body = build_composite_email_body(item, intro)

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


def apply_inspection_filters_and_paginate(request, base_queryset):
    """
    Применяет фильтры по дате и нарушениям к QuerySet отчетов (Inspection).
    Делает пагинацию по 20 элементов.
    Возвращает словарь с page_obj и текущими фильтрами для шаблона.
    """
    queryset = base_queryset

    # 1. Читаем параметры из URL (GET), заменяем None на пустую строку ''
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    only_violations = request.GET.get("only_violations", "")

    # 2. Применяем фильтры
    if date_from:
        queryset = queryset.filter(date_check__gte=date_from)

    if date_to:
        queryset = queryset.filter(date_check__lte=date_to)

    if only_violations == "on":
        # Предполагается, что queryset уже аннотирован .annotate(violation_count=...)
        queryset = queryset.filter(violation_count__gt=0)

    # 3. Пагинация
    paginator = Paginator(queryset, 20)  # 20 штук на страницу
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # 4. Возвращаем готовый кусок контекста
    return {
        "page_obj": page_obj,
        "filter_date_from": date_from,
        "filter_date_to": date_to,
        "filter_only_violations": only_violations,
    }


def is_global_viewer(user):
    """
    Определяет, имеет ли пользователь право видеть ВСЕ отчеты на заводе.
    """
    # 1. Глобальные роли по умолчанию
    if user.role in [
        User.ROLE_PRODUCTION_CHIEF,
        User.ROLE_ENGINEER_CHIEF,
        User.ROLE_ADMIN,
    ]:
        return True

    # 2. Суперпользователи Django
    if user.is_superuser:
        return True

    # 3. Привилегия участка ЭМО
    # Проверяем, является ли пользователь начальником/мастером на участке,
    # название которого содержит "ЭМО".
    # (Используем name__icontains="ЭМО" для гибкости, вдруг участок назовут "Участок ЭМО")

    is_emo_boss = Location.objects.filter(
        Q(name__icontains="ЭМО") | Q(name__icontains="Энерго-механический отдел"),
        Q(manager=user) | Q(deputies=user) | Q(senior_masters=user) | Q(masters=user),
    ).exists()

    if is_emo_boss:
        return True

    # Иначе - это обычный локальный босс (или рабочий)
    return False
