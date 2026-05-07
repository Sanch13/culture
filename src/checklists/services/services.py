import calendar
import datetime
from pathlib import Path
from itertools import groupby

import holidays
import structlog

from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Q, Avg, Min
from django.utils import timezone
from django.core.paginator import Paginator

from checklists.models import (
    Inspection,
    InspectionItem,
    Schedule,
    InspectionRoute,
    SwapLog,
    Location,
    InspectionSectionScore,
    LocationDailyScore,
    CalendarOverride,
    ScheduleGeneratorState,
)
from checklists.utils import format_phone_number

User = get_user_model()
logger = structlog.get_logger(__name__)


def generate_schedule(start_date, days_count=21):
    """
    Генерирует расписание на основе МАРШРУТОВ с использованием стабильной очереди.
    """
    log = logger.bind(
        service="schedule_generator", start_date=str(start_date), days_count=days_count
    )

    log.info("generator_started - Старт генерации расписания")

    routes = list(
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )
    inspectors = list(
        User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id")
    )

    if not routes:
        log.error("generation_failed_no_routes - Ошибка: Не настроены Маршруты.")
        return "Ошибка: Не настроены Маршруты."
    if not inspectors:
        log.error(
            "generation_failed_no_inspectors - Ошибка: Нет сотрудников-инспекторов."
        )
        return "Ошибка: Нет сотрудников-инспекторов."

    log.info(
        "resources_loaded - Ресурсы загружены",
        routes_count=len(routes),
        inspectors_count=len(inspectors),
    )

    with transaction.atomic():
        # --- ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА (УМНАЯ ЗАКЛАДКА) ---
        # 1. Берём state с блокировкой
        state, _ = ScheduleGeneratorState.objects.select_for_update().get_or_create(
            id=1
        )

        # 2. Определяем start_index
        start_index = 0
        if state.last_user_id > 0:
            # 1. Пытаемся найти точный индекс последнего человека в текущем списке
            found = False
            for idx, inspector in enumerate(inspectors):
                if inspector.id == state.last_user_id:
                    start_index = (idx + 1) % len(inspectors)
                    found = True
                    log.info(
                        "queue_state_found - Указатель очереди найден",
                        last_user_id=state.last_user_id,
                        next_index=start_index,
                    )
                    break

            # 2. ЕСЛИ ЧЕЛОВЕКА УВОЛИЛИ (Критический случай)
            if not found:
                # Ищем первого человека, чей ID больше уволенного
                # (так как список отсортирован по ID, это будет тот, кто стоял сразу за ним)
                log.warning(
                    "queue_state_user_missing - Утерян указатель. Возможно уволен, Берем следующего в очереди",
                    missing_user_id=state.last_user_id,
                )
                next_survivor = None
                for idx, inspector in enumerate(inspectors):
                    if inspector.id > state.last_user_id:
                        next_survivor = idx
                        break

                if next_survivor is not None:
                    start_index = next_survivor  # Начинаем прямо с него
                    log.info(
                        "queue_state_recovered - Новый указатель найден (восстановлен)",
                        next_survivor_id=inspectors[start_index].id,
                    )
                else:
                    # Если уволенный был самым последним в списке,
                    # значит следующий выживший - это самый первый в списке (0).
                    start_index = 0
                    log.info(
                        "queue_state_reset_to_zero - Указатель на очередь сброшен в 0"
                    )
        else:
            log.info("queue_state_initial_start")

        current_inspector_idx = start_index
        created_total = 0
        current_date = start_date

        for _ in range(days_count):
            if not is_working_day(current_date):
                log.info(
                    "day_skipped - День пропущен. Не рабочий день",
                    date=str(current_date),
                    reason="not_working_day",
                )
                current_date += datetime.timedelta(days=1)
                continue

            day_log = log.bind(date=str(current_date))
            day_assigned_count = 0

            for route in routes:
                inspector = inspectors[current_inspector_idx]

                templates_in_route = route.templates.all()
                if not templates_in_route:
                    day_log.warning("route_empty - Пустой маршрут", route_id=route.id)
                    continue

                assigned_something = False
                for tmpl in templates_in_route:
                    if not Schedule.objects.filter(
                        date=current_date, template=tmpl
                    ).exists():
                        Schedule.objects.create(
                            date=current_date, template=tmpl, inspector=inspector
                        )
                        created_total += 1
                        assigned_something = True
                        day_assigned_count += 1
                    else:
                        day_log.info(
                            "slot_already_taken - Маршрут уже занят", template=tmpl.name
                        )

                # Сдвигаем очередь только если назначили маршрут
                if assigned_something:
                    day_log.info(
                        "route_assigned - Маршрут назначен",
                        route_id=route.id,
                        router_name=route.title,
                        inspector_id=inspector.id,
                        inspector_name=inspector.get_full_name(),
                    )
                    current_inspector_idx = (current_inspector_idx + 1) % len(
                        inspectors
                    )

            day_log.info(
                "day_processed - Маршруты на день назначены",
                routes_assigned=day_assigned_count,
            )
            current_date += datetime.timedelta(days=1)

        # --- СОХРАНЯЕМ ЗАКЛАДКУ ДЛЯ СЛЕДУЮЩЕГО РАЗА ---
        if created_total > 0:
            # Вычисляем, кто реально получил последнюю задачу
            # (отнимаем 1, потому что индекс уже сдвинулся на следующего)
            last_assigned_idx = (current_inspector_idx - 1) % len(inspectors)
            state.last_user_id = inspectors[last_assigned_idx].id
            state.save()
            log.info(
                "queue_state_saved - Указатель очереди сохранен",
                new_last_user_id=state.last_user_id,
            )
        else:
            log.info(
                "queue_state_unchanged - Указатель очереди не назначен",
                reason="no_records_created",
            )

    log.info(
        "generator_finished - Генератор закончил формировать расписание",
        total_created=created_total,
    )
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
                    criteria_origin=criteria,
                    section_name=section.title,
                    section_type=section.section_type,
                    criteria_text=criteria.text,
                    criteria_order=criteria.order,
                    is_compliant=True,
                )

        return inspection


def perform_auto_swap(schedule_item, reason_type, reason_text):
    """
    Групповая автозамена.
    Меняет ВСЕ смены инициатора на эту дату на ВСЕ смены жертвы на будущую дату.
    """
    current_date = schedule_item.date
    current_user = schedule_item.inspector

    log = logger.bind(
        service="perform_auto_swap",
        initiator_id=current_user.id,
        source_date=str(current_date),
    )

    log.info("service_started")

    # 1. Находим ВСЕ задачи инициатора на этот день (чтобы отдать их все)
    current_tasks = list(
        Schedule.objects.filter(date=current_date, inspector=current_user)
    )

    log.info("initiator_tasks_found", count=len(current_tasks))

    # Защита: вдруг хоть один из отчетов уже начат? (Хотя view это уже проверила)
    for t in current_tasks:
        if t.inspection:
            log.warning(
                "service_aborted",
                reason="partially_started_tasks_found",
                problem_task_id=t.id,
            )
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

    log.info("searching_candidate", search_from_date=str(start_of_next_week))

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
        log.error("service_failed", reason="no_candidates_available")
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

    log = log.bind(target_user_id=target_user.id, target_date=str(target_date))
    log.info("candidate_found", target_tasks_count=len(target_tasks))

    # 5. СОВЕРШАЕМ ОБМЕН
    try:
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
            reason_dict = {
                "vacation": "Трудовой отпуск",
                "trip": "Командировка",
                "sick": "Больничный",
                "other": "Другое",
            }
            human_reason = reason_dict.get(reason_type, "Неизвестно")

            final_reason_str = f"{human_reason}"
            if reason_text:
                final_reason_str += f" ({reason_text})"

            SwapLog.objects.create(
                requestor=current_user,
                target_user=target_user,
                source_date=current_date,
                target_date=target_date,
                reason_type=reason_type,
                reason=final_reason_str,
            )
            log.info("db_transaction_committed", swap_reason=final_reason_str)

    except Exception as e:
        log.error("db_transaction_failed", error=str(e), exc_info=True)
        return (
            False,
            "Произошла ошибка при сохранении данных в БД.",
            "",
        )

    info_about_change = (
        f"Дата: {current_date}.\nПроверяющий {current_user.last_name} {current_user.first_name} "
        f"заменился на {target_user.last_name} {target_user.first_name}.\nПричина: {final_reason_str}"
    )

    log.info("service_completed_successfully")
    return (
        True,
        f"Обмен выполнен. Вы перенесены на {target_date.strftime('%d.%m')}. Вместо вас выйдет {target_user.last_name} {target_user.first_name}.",
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
    target_date = schedules[0].date
    date_str = schedules[0].date.strftime("%d.%m.%Y")

    # Название дня недели (0=Пн, 1=Вт...)
    weekdays_ru = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]
    day_name = weekdays_ru[target_date.weekday()]

    # 1. Шапка
    body = [
        f"Здравствуйте, {inspector.first_name}!\n",
        f"{intro_message}\n",
        f"📅 Дата смены: {date_str} ({day_name})\n",
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

    body.append("------------------------")
    body.append("Перейти к приложению https://culture.miran.by/auth/login/")
    body.append("------------------------")
    body.append(
        "Если вы не можете выполнить проверку, воспользуйтесь кнопкой 'Автозамена' в личном кабинете."
    )
    body.append("Пожалуйста, не забудьте заполнить отчеты в системе.")

    return "\n".join(body)


def get_swap_notification_data(date_str, user_id):
    """
    Собирает данные для письма на основе ВСЕХ задач инспектора на конкретный день.
    """
    log = logger.bind(
        service="get_swap_notification_data",
        date_str=date_str,
    )
    log.info("Load all tasks - Загружаем все задачи (расписание) человека на этот день")

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

    log.info(
        "Tasks loaded successfully - Расписание задач на выбранный день загружен",
        tasks_lenth=len(schedules),
        inspector=schedules[0].inspector,
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
    log = logger.bind(service="prepare_notifications", date=str(target_date or "today"))

    if target_date is None:
        target_date = datetime.date.today()

    # 1. ЗАГРУЖАЕМ РАСПИСАНИЕ
    # ОБЯЗАТЕЛЬНО добавляем order_by('inspector_id') для правильной работы groupby!
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
            "template__location__deputies",
            "template__location__senior_masters",
        )
        .order_by("inspector_id")
    )

    log.info("fetching_schedules", found_count=schedules.count())

    notifications_data = []

    # 2. ГРУППИРУЕМ ЗАДАЧИ ПО ИНСПЕКТОРУ
    # groupby собирает все задачи одного инспектора вместе
    for inspector, items_iter in groupby(schedules, key=lambda s: s.inspector):
        if not inspector.email:
            log.warning("inspector_missing_email", inspector_id=inspector.id)
            continue

        # Превращаем итератор в полноценный список
        # Например: user_tasks = [Задача 1 (Раздув), Задача 2 (ЦПМ)]
        user_tasks = list(items_iter)

        intro = "Напоминаем, что у вас запланирована плановая проверка."

        body = build_composite_email_body(user_tasks, intro)

        # Берем первый шаблон из списка задач сотрудника
        first_template = user_tasks[0].template

        # Ищем маршрут, к которому привязан этот шаблон
        # (Используем .first(), так как мы предполагаем, что шаблон входит в 1 маршрут)
        route = first_template.routes.first()

        if route:
            # Если маршрут найден - пишем его имя
            route_name = route.title
        else:
            # Если маршрута почему-то нет (например, шаблон назначен вручную без маршрута)
            # - пишем просто "Несколько участков" или название первого участка
            route_name = first_template.location.name

        subject = f"Напоминание о проверке: {route_name}"

        notifications_data.append(
            {
                "recipient_email": inspector.email,
                "subject": subject,
                "body": body,
            }
        )
    log.info("notifications_built", total_ready=len(notifications_data))
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
        User.ROLE_OBSERVER,
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


def calculate_inspection_score(inspection):
    """
    ЭТАП 1: Высчитываем баллы для каждого РАЗДЕЛА (A, B1, B2, C...)
    ЭТАП 2: Высчитываем итоговый балл ОТЧЕТА.
    """
    items = inspection.items.all()
    if not items:
        return

    # --- 1. ОЦЕНКА РАЗДЕЛОВ ---

    # Группируем вопросы по типам разделов (A, B1, C...)
    sections_dict = {}
    for item in items:
        if not item.is_compliant:
            if item.is_repeated_violation:
                yesterday_item = (
                    InspectionItem.objects.filter(
                        inspection__template=inspection.template,
                        inspection__is_completed=True,
                        inspection__date_check__lt=inspection.date_check,
                        criteria_origin_id=item.criteria_origin_id,
                    )
                    .order_by("-inspection__date_check")
                    .first()
                )

                item.consecutive_violations = (
                    (yesterday_item.consecutive_violations + 1) if yesterday_item else 2
                )
            else:
                item.consecutive_violations = 1
        else:
            item.consecutive_violations = 0

        item.save(update_fields=["consecutive_violations"])

        stype = getattr(item, "section_type", "GENERAL")
        if not stype:
            stype = "GENERAL"

        if stype not in sections_dict:
            sections_dict[stype] = {"name": item.section_name, "items": []}

        sections_dict[stype]["items"].append(item)

    InspectionSectionScore.objects.filter(inspection=inspection).delete()

    # Словарь для хранения посчитанных баллов: {'A': 5.0, 'B1': 3.0, 'C': 6.0}
    calculated_section_scores = {}

    for stype, data in sections_dict.items():
        # Ищем самую большую серию повторов внутри этого раздела
        max_repeats = max([i.consecutive_violations for i in data["items"]] + [0])

        has_repeat_penalty = False

        # ЛОГИКА ОЦЕНКИ РАЗДЕЛА (по твоему ТЗ)
        if max_repeats >= 3:
            score = 2.0  # Двойной повтор (и более)
            has_repeat_penalty = True
        elif max_repeats == 2:
            score = 3.0  # Первый повтор
            has_repeat_penalty = True
        else:
            # Повторов нет. Считаем обычные нарушения (долю)
            total_q = len(data["items"])
            bad_q = sum(1 for i in data["items"] if not i.is_compliant)
            score = (
                round(6.0 * ((total_q - bad_q) / total_q), 2) if total_q > 0 else 6.0
            )

        # Сохраняем балл Раздела в БД
        InspectionSectionScore.objects.create(
            inspection=inspection,
            date_check=inspection.date_check,
            section_name=data["name"],
            section_type=stype,
            score=score,
        )
        calculated_section_scores[stype] = {
            "score": score,
            "has_repeat_penalty": has_repeat_penalty,
        }

    # --- 2. ОЦЕНКА ОТЧЕТА В ЦЕЛОМ ---

    # Исключаем B1 и D (они не влияют на средний балл текущего отчета)
    report_scores_data = [
        data
        for stype, data in calculated_section_scores.items()
        if stype
        not in [
            "B1",
        ]
    ]

    if not report_scores_data:
        final_report_score = 6.0  # Если отчет состоял только из B1 или D
    else:
        # Проверяем жесткое правило: Если хоть один раздел (A, B2, C) получил 3 или 2,
        # то весь отчет получает этот низший балл!
        # Ищем, был ли хоть в одном учитываемом разделе (A, B2, C, D) штраф за повтор
        penalties = [d["score"] for d in report_scores_data if d["has_repeat_penalty"]]

        if penalties:
            # Если есть штрафы (3.0 или 2.0), берем самый жесткий (минимальный)
            # ТЗ: "Если было повторяющиеся нарушение... просто ставится общая оценка 3/2"
            final_report_score = min(penalties)
        else:
            # Если штрафов за повтор нет -> просто считаем честное среднее
            # (даже если там есть 1.0 за кучу новых нарушений)
            all_scores = [d["score"] for d in report_scores_data]
            final_report_score = sum(all_scores) / len(all_scores)

    # Сохраняем балл Отчета в БД
    inspection.final_score = round(final_report_score, 2)
    inspection.save(update_fields=["final_score"])

    return inspection.final_score


def calculate_daily_location_scores(target_date):
    """
    ЭТАП 3: Высчитываем итоговый балл каждого УЧАСТКА за день.
    """
    locations = Location.objects.all()

    # 1. ГЛОБАЛЬНЫЙ B1 (Для ЭМО)
    # Берем ВСЕ сохраненные баллы разделов B1 за сегодня по всему заводу
    all_b1_scores = InspectionSectionScore.objects.filter(
        date_check=target_date, section_type="B1"
    )

    global_b1_score = None
    if all_b1_scores.exists():
        # Если где-то есть повтор (штраф 3 или 2) - он роняет весь B1 завода
        min_b1 = all_b1_scores.aggregate(Min("score"))["score__min"]
        if min_b1 <= 3.01:
            global_b1_score = min_b1
        else:
            global_b1_score = all_b1_scores.aggregate(Avg("score"))["score__avg"]

    # 2. РАСЧЕТ БАЛЛОВ УЧАСТКОВ
    for loc in locations:
        final_loc_score = None

        # --- ЛОГИКА ЭМО ---
        if "ЭМО" in loc.name:
            # Берем балл раздела D (из отчетов этого участка)
            score_d = InspectionSectionScore.objects.filter(
                inspection__template__location=loc,
                date_check=target_date,
                section_type="D",
            ).aggregate(Avg("score"))["score__avg"]

            # Считаем среднее между D и глобальным B1
            if score_d is not None and global_b1_score is not None:
                final_loc_score = (score_d + global_b1_score) / 2
            elif score_d is not None:
                final_loc_score = score_d
            elif global_b1_score is not None:
                final_loc_score = global_b1_score

        # --- ЛОГИКА ОСТАЛЬНЫХ УЧАСТКОВ (УПП, Сборка, Инструментальный...) ---
        else:
            # Берем ГОТОВЫЕ баллы (final_score) всех сданных отчетов этого участка
            inspections = Inspection.objects.filter(
                template__location=loc,
                date_check=target_date,
                is_completed=True,
                final_score__isnull=False,
            )

            if inspections.exists():
                # Просто считаем среднее арифметическое отчетов (например, 4-х отчетов УПП)
                scores = [insp.final_score for insp in inspections]
                final_loc_score = sum(scores) / len(scores)

        # 3. СОХРАНЯЕМ БАЛЛ УЧАСТКА В БД
        LocationDailyScore.objects.update_or_create(
            location=loc,
            date=target_date,
            defaults={
                "score": round(final_loc_score, 2)
                if final_loc_score is not None
                else None
            },
        )


def is_working_day(target_date):
    """
    Возвращает True, если день рабочий. Учитывает:
    1. Ручные переносы (CalendarOverride) - наивысший приоритет.
    2. Праздники (holidays.BY).
    3. Стандартные выходные (Суббота, Воскресенье).
    """
    # 1. Проверяем ручные переопределения (Приоритет №1)
    override = CalendarOverride.objects.filter(date=target_date).first()
    if override:
        return override.day_type == CalendarOverride.TYPE_WORKDAY

    # 2. Если ручных настроек нет, смотрим стандартный календарь

    # А. Праздники РБ (Приоритет №2)
    by_holidays = holidays.BY(years=target_date.year)
    if target_date in by_holidays:
        return False  # Праздник = не работаем

    # Б. Обычные выходные (Приоритет №3)
    # 5 = Суббота, 6 = Воскресенье
    if target_date.weekday() >= 5:
        return False  # Выходной = не работаем

    # Если ничего не совпало - это обычный Пн-Пт
    return True


def prepare_weekly_notifications():
    """
    Данные для ЕЖЕНЕДЕЛЬНОЙ рассылки (План на следующую неделю).
    Запускается в пятницу.
    """
    today = timezone.now().date()

    # 1. Вычисляем диапазон СЛЕДУЮЩЕЙ недели (Пн - Вс)
    # Если сегодня пятница (4), до следующего понедельника 3 дня.
    days_until_next_monday = 7 - today.weekday()
    start_of_next_week = today + datetime.timedelta(days=days_until_next_monday)
    end_of_next_week = start_of_next_week + datetime.timedelta(days=6)

    # 2. Загружаем расписание на следующую неделю
    # Сортировка по inspector_id ОБЯЗАТЕЛЬНА для groupby
    schedules = (
        Schedule.objects.filter(date__range=[start_of_next_week, end_of_next_week])
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
        .order_by(
            "inspector_id", "date"
        )  # Сортируем по юзеру, а внутри юзера - по дате
    )

    notifications_data = []

    # 3. Группируем задачи по каждому сотруднику
    for inspector, items_iter in groupby(schedules, key=lambda s: s.inspector):
        if not inspector.email:
            continue

        user_tasks = list(items_iter)

        # 4. Формируем тело письма (Дайджест)
        # Так как задач может быть много (на Пн, Ср, Пт), мы не можем использовать
        # стандартный build_composite_email_body (он рассчитан на один день).
        # Напишем кастомный сборщик дайджеста.

        body_lines = [
            f"Здравствуйте, {inspector.first_name}!\n",
            f"Направляем вам график плановых проверок на следующую неделю ({start_of_next_week.strftime('%d.%m')} - {end_date_str_format(end_of_next_week)}).\n",
            "📋 ВАШ ПЛАН:\n",
        ]

        unique_locations = set()

        # Группируем задачи сотрудника еще и по дням (чтобы было красиво: Понедельник: Раздув, Вторник: ЦПМ)
        for date_key, day_tasks_iter in groupby(user_tasks, key=lambda t: t.date):
            day_tasks = list(day_tasks_iter)

            # Название дня недели (0=Пн, 1=Вт...)
            weekdays_ru = [
                "Понедельник",
                "Вторник",
                "Среда",
                "Четверг",
                "Пятница",
                "Суббота",
                "Воскресенье",
            ]
            day_name = weekdays_ru[date_key.weekday()]

            body_lines.append(f"📅 {day_name} ({date_key.strftime('%d.%m')}):")

            for task in day_tasks:
                loc = task.template.location
                unique_locations.add(loc)
                body_lines.append(f"   - {task.template.name} (Участок: {loc.name})")
            body_lines.append("")  # Пустая строка между днями

        # 5. Добавляем контакты руководства (для всех участков, куда он пойдет на неделе)
        body_lines.append("--- КОНТАКТЫ ДЛЯ СВЯЗИ ---")
        for loc in unique_locations:
            body_lines.append(f"\n🏢 {loc.name.upper()}")
            body_lines.append(_get_location_contacts_text(loc))

        # Добавляем главного босса
        body_lines.append(_get_production_chief_text())

        body_lines.append("------------------------")
        body_lines.append("Перейти к приложению https://culture.miran.by/auth/login/")
        body_lines.append("------------------------")
        body_lines.append(
            "Если вы не можете выполнить проверку, воспользуйтесь кнопкой 'Автозамена' в личном кабинете."
        )
        body_lines.append(
            "Пожалуйста, планируйте свое рабочее время с учетом этого графика."
        )

        # 6. Добавляем готовое письмо в список рассылки
        notifications_data.append(
            {
                "recipient_email": inspector.email,
                "subject": f"🗓 Ваш график проверок на неделю ({start_of_next_week.strftime('%d.%m')} - {end_of_next_week.strftime('%d.%m')})",
                "body": "\n".join(body_lines),
            }
        )

    return notifications_data


def end_date_str_format(date_obj):
    return date_obj.strftime("%d.%m")


def prepare_monday_reminders():
    """
    Данные для рассылки В ПОНЕДЕЛЬНИК утром.
    Напоминает о проверках на ТЕКУЩЕЙ неделе (со Вторника по Воскресенье).
    """
    today = timezone.now().date()

    # 1. Вычисляем диапазон (Завтра - Конец недели)
    # Сегодня понедельник, значит начинаем со вторника (+1 день)
    start_date = today + datetime.timedelta(days=1)

    # Конец недели (Воскресенье)
    days_until_sunday = 6 - today.weekday()
    end_date = today + datetime.timedelta(days=days_until_sunday)

    # 2. Загружаем расписание
    schedules = (
        Schedule.objects.filter(date__range=[start_date, end_date])
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
        .order_by("inspector_id", "date")
    )

    notifications_data = []

    # 3. Группируем по инспектору (как мы делали в пятничной рассылке)
    for inspector, items_iter in groupby(schedules, key=lambda s: s.inspector):
        if not inspector.email:
            continue

        user_tasks = list(items_iter)

        # 4. Формируем текст письма
        body_lines = [
            f"Здравствуйте, {inspector.first_name}!\n",
            "Напоминаем ваш график плановых проверок на эту неделю:\n",
        ]

        unique_locations = set()

        # Группируем по дням
        for date_key, day_tasks_iter in groupby(user_tasks, key=lambda t: t.date):
            day_tasks = list(day_tasks_iter)

            weekdays_ru = [
                "Понедельник",
                "Вторник",
                "Среда",
                "Четверг",
                "Пятница",
                "Суббота",
                "Воскресенье",
            ]
            day_name = weekdays_ru[date_key.weekday()]

            body_lines.append(f"📅 {day_name} ({date_key.strftime('%d.%m')}):")

            for task in day_tasks:
                loc = task.template.location
                unique_locations.add(loc)
                body_lines.append(f"   - {task.template.name} (Участок: {loc.name})")
            body_lines.append("")

            # 5. Контакты руководства
        body_lines.append("--- КОНТАКТЫ ДЛЯ СВЯЗИ ---")
        for loc in unique_locations:
            body_lines.append(f"\n🏢 {loc.name.upper()}")
            body_lines.append(_get_location_contacts_text(loc))

        body_lines.append(_get_production_chief_text())
        body_lines.append("------------------------")
        body_lines.append("Перейти к приложению https://culture.miran.by/auth/login/")
        body_lines.append("------------------------")
        body_lines.append(
            "Если вы не можете выполнить проверку, воспользуйтесь кнопкой 'Автозамена' в личном кабинете."
        )
        body_lines.append(
            "Пожалуйста, планируйте свое рабочее время с учетом этого графика."
        )

        # 6. Добавляем в список отправки
        notifications_data.append(
            {
                "recipient_email": inspector.email,
                "subject": "🔔 Напоминание: График проверок на эту неделю",
                "body": "\n".join(body_lines),
            }
        )

    return notifications_data


def prepare_overdue_notifications():
    """
    Ищет все невыполненные (или неначатые) проверки за СЕГОДНЯ.
    Возвращает текст письма для администраторов.
    """
    today = timezone.now().date()

    # Если сегодня выходной/праздник по нашей логике - пропускаем
    if not is_working_day(today):
        return None

    # Ищем записи в расписании на сегодня, у которых:
    # 1. Вообще нет отчета (inspection is null)
    # ИЛИ
    # 2. Отчет есть, но он не завершен (inspection__is_completed=False)
    overdue_schedules = (
        Schedule.objects.filter(date=today)
        .filter(Q(inspection__isnull=True) | Q(inspection__is_completed=False))
        .select_related("inspector", "template", "template__location")
        .order_by("template__location__name", "inspector__last_name")
    )

    if not overdue_schedules.exists():
        return None  # Все молодцы, долгов нет

    # Формируем тело письма для Админов
    body_lines = [
        "Уважаемый администратор,\n",
        f"По состоянию на 14:00 сегодняшнего дня ({today.strftime('%d.%m.%Y')}) ",
        "следующие плановые проверки НЕ ЗАВЕРШЕНЫ:\n",
    ]

    # Группируем по участкам для читаемости
    for location, tasks_iter in groupby(
        overdue_schedules, key=lambda s: s.template.location
    ):
        body_lines.append(f"\n🏢 {location.name.upper()}")

        for task in tasks_iter:
            status = "⏳ В процессе (Черновик)" if task.inspection else "❌ Не начато"
            body_lines.append(
                f"   - {task.inspector.get_full_name()} -> {task.template.name} [{status}]"
            )

    body_lines.append("\n------------------------")
    body_lines.append("Пожалуйста, свяжитесь с ответственными сотрудниками.")

    return "\n".join(body_lines)


def analytics_dashboard(today, year, month):
    # Динамический список лет (например, от 2024 до текущего + 1)
    years_choices = list(range(2025, today.year + 2))

    # 2. РАБОЧИЕ ДНИ МЕСЯЦА (С учетом праздников РБ)
    # by_holidays = holidays.BY(years=year)  # Загружаем праздники за выбранный год

    _, num_days = calendar.monthrange(year, month)

    work_days = []
    for day in range(1, num_days + 1):
        current_date = datetime.date(year, month, day)

        # # Проверяем: если не выходной (0-4 это Пн-Пт) И не праздник РБ
        # if current_date.weekday() < 5 and current_date not in by_holidays:
        #     work_days.append(current_date)

        # --- ИСПОЛЬЗУЕМ НАШУ УМНУЮ ФУНКЦИЮ ---
        if is_working_day(current_date):
            work_days.append(current_date)

    # 3. ЗАГРУЗКА ДАННЫХ ИЗ БД
    loc_scores = LocationDailyScore.objects.filter(date__year=year, date__month=month)
    loc_map = {(s.location_id, s.date): s.score for s in loc_scores}

    inspections = Inspection.objects.filter(
        date_check__year=year, date_check__month=month, is_completed=True
    ).select_related("template", "template__location")

    insp_map = {}
    for insp in inspections:
        key = (insp.template_id, insp.date_check)
        if key not in insp_map:
            insp_map[key] = []
        if insp.final_score is not None:
            insp_map[key].append(insp.final_score)

    for key, scores in insp_map.items():
        insp_map[key] = sum(scores) / len(scores) if scores else None

    # Глобальный B1
    b1_scores = InspectionSectionScore.objects.filter(
        date_check__year=year, date_check__month=month, section_type="B1"
    )
    b1_map = {}
    for b1 in b1_scores:
        day = b1.date_check
        if day not in b1_map:
            b1_map[day] = []
        if b1.score is not None:
            b1_map[day].append(b1.score)

    for day, scores in b1_map.items():
        if scores:
            if min(scores) <= 3.01:
                b1_map[day] = min(scores)
            else:
                b1_map[day] = sum(scores) / len(scores)
        else:
            b1_map[day] = None

    # 4. СБОРКА ТАБЛИЦЫ
    table_rows = []
    locations = Location.objects.all().order_by("name")

    for loc in locations:
        # Участок
        loc_row = {
            "is_location": True,
            "title": loc.name,
            "scores": [],
            "avg_month": None,
        }
        loc_sum = 0
        loc_count = 0
        for day in work_days:
            score = loc_map.get((loc.id, day))
            loc_row["scores"].append(score)
            if score is not None:
                loc_sum += score
                loc_count += 1
        if loc_count > 0:
            loc_row["avg_month"] = round(loc_sum / loc_count, 2)
        table_rows.append(loc_row)

        # Отчеты
        templates = loc.templates.all().order_by("name")
        for tmpl in templates:
            tmpl_row = {
                "is_location": False,
                "title": tmpl.name,
                "scores": [],
                "avg_month": None,
            }
            tmpl_sum = 0
            tmpl_count = 0
            for day in work_days:
                score = insp_map.get((tmpl.id, day))
                tmpl_row["scores"].append(score)
                if score is not None:
                    tmpl_sum += score
                    tmpl_count += 1
            if tmpl_count > 0:
                tmpl_row["avg_month"] = round(tmpl_sum / tmpl_count, 2)
            table_rows.append(tmpl_row)

    # Состояние B1
    b1_row = {
        "is_location": True,
        "title": "Состояние оборудования на участках (B1)",
        "scores": [],
        "avg_month": None,
    }
    b1_sum = 0
    b1_count = 0
    for day in work_days:
        score = b1_map.get(day)
        b1_row["scores"].append(score)
        if score is not None:
            b1_sum += score
            b1_count += 1
    if b1_count > 0:
        b1_row["avg_month"] = round(b1_sum / b1_count, 2)
    table_rows.append(b1_row)

    # 5. ФОРМИРУЕМ МЕСЯЦЫ
    months_choices = [
        (1, "Январь"),
        (2, "Февраль"),
        (3, "Март"),
        (4, "Апрель"),
        (5, "Май"),
        (6, "Июнь"),
        (7, "Июль"),
        (8, "Август"),
        (9, "Сентябрь"),
        (10, "Октябрь"),
        (11, "Ноябрь"),
        (12, "Декабрь"),
    ]

    context = {
        "table_rows": table_rows,
        "work_days": work_days,
        "current_year": year,
        "current_month": month,
        "months_choices": months_choices,
        "years_choices": years_choices,  # Передаем года
    }

    return context


def get_item_history_chain(item, max_depth=2):
    """
    Раскручивает цепочку прошлых нарушений (Вчера, Позавчера...).
    Возвращает список объектов InspectionItem.
    """
    history_chain = []
    current_item = item

    # Если текущий пункт - это НЕ повторное нарушение, истории нет
    if not current_item.is_repeated_violation or current_item.is_compliant:
        return history_chain

    # Идем в прошлое на max_depth шагов
    for _ in range(max_depth):
        past_item = (
            InspectionItem.objects.filter(
                inspection__template=current_item.inspection.template,
                inspection__is_completed=True,
                inspection__date_check__lt=current_item.inspection.date_check,
                criteria_origin_id=current_item.criteria_origin_id,
                is_compliant=False,  # Берем только нарушения
            )
            .prefetch_related("photos")
            .order_by("-inspection__date_check")
            .first()
        )

        if past_item:
            history_chain.append(past_item)
            current_item = past_item  # Теперь ищем "вчера" для этого "вчера"
        else:
            break  # Цепочка прервалась (или не было отчета, или было ОК)

    return history_chain


def format_size(bytes_size):
    """Конвертирует байты в человекочитаемый формат: 1024 → '1.00 KB'"""
    if bytes_size is None:
        return "0 B"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def get_file_extension(filename):
    """Возвращает расширение файла в нижнем регистре без точки: 'photo.JPG' → 'jpg'"""
    if not filename:
        return "unknown"
    return Path(filename).suffix.lower().lstrip(".") or "unknown"
