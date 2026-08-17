from datetime import timedelta, datetime

import structlog

from django.core.paginator import Paginator
from django.core.cache import cache
from django.shortcuts import render, get_object_or_404
from django.db.models import Count, Q
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from checklists.decorators import admin_required
from checklists.services.services import (
    apply_inspection_filters_and_paginate,
    analytics_dashboard,
    get_item_history_chain,
)
from checklists.services.gen_schedule_with_balance import generate_schedule
from checklists.models import (
    ChecklistTemplate,
    Inspection,
    Schedule,
    SwapLog,
    InspectionRoute,
    Location,
)
from checklists.tasks import notify_user_about_swap, task_calculate_score

User = get_user_model()

logger = structlog.get_logger(__name__)


# --- ЗОНА АДМИНИСТРАТОРА (Строгий режим) ---
@admin_required
def admin_dashboard(request):
    today = timezone.now().date()

    # 1. Читаем параметры из GET-запроса
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    # 2. Парсим начальную дату (По умолчанию: 1 число текущего месяца)
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = today.replace(day=1)
    else:
        start_date = today.replace(day=1)

    # 3. Парсим конечную дату (По умолчанию: сегодня)
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            end_date = today
    else:
        end_date = today

    inspectors_stats = (
        User.objects.filter(is_active=True, can_perform_inspections=True)
        .annotate(
            completed_count=Count(
                "inspection__date_check",
                distinct=True,
                filter=Q(
                    inspection__is_completed=True,
                    inspection__date_check__gte=start_date,
                    inspection__date_check__lte=end_date,
                ),
            )
        )
        .order_by("-completed_count")
    )

    total_templates = ChecklistTemplate.objects.count()
    context = {
        "total_templates": total_templates,
        "filter_start_date": start_date.strftime("%Y-%m-%d"),
        "filter_end_date": end_date.strftime("%Y-%m-%d"),
        "inspectors_stats": inspectors_stats,
    }
    return render(request, "checklists/admin_dashboard.html", context)


@admin_required
def admin_templates(request):
    templates = ChecklistTemplate.objects.all().select_related("location")
    context = {"templates": templates}
    return render(request, "checklists/admin_templates.html", context)


@admin_required
def template_preview(request, template_id):
    template = get_object_or_404(ChecklistTemplate, pk=template_id)
    sections = template.sections.all().order_by("order").prefetch_related("criteria")
    context = {"template": template, "sections": sections}
    return render(request, "checklists/template_preview.html", context)


@admin_required
def admin_inspection_list(request):
    """
    Журнал всех завершенных проверок.
    """
    # 1. Базовый запрос (Все завершенные отчеты)
    queryset = (
        Inspection.objects.filter(is_completed=True)
        .select_related("inspector", "template")
        .annotate(violation_count=Count("items", filter=Q(items__is_compliant=False)))
        .order_by("-date_check", "-created_at")
    )

    # 2. Вызываем наш сервис фильтрации
    context = apply_inspection_filters_and_paginate(request, queryset)

    return render(request, "checklists/admin_history.html", context)


@admin_required
def admin_inspection_detail(request, inspection_id):
    """
    Просмотр конкретного отчета (Read-Only) с историей повторов.
    """
    inspection = get_object_or_404(Inspection, id=inspection_id)

    # 1. Загружаем пункты (используем ту же логику сортировки, что и при заполнении!)
    items = list(
        inspection.items.select_related("criteria_origin__section")
        .prefetch_related("photos")
        .all()
    )

    items.sort(
        key=lambda i: (
            i.criteria_origin.section.order
            if i.criteria_origin and i.criteria_origin.section
            else 9999,
            i.criteria_order,
        )
    )

    # 2. Подтягиваем историю (цепочку) для пунктов с повторными нарушениями
    for item in items:
        # Если галочка "Повторное" стоит, вытягиваем 2 дня в прошлое (Вчера и Позавчера)
        if item.is_repeated_violation:
            # Создаем на лету атрибут history_chain (список)
            item.history_chain = get_item_history_chain(item, max_depth=2)

    # 3. Группировка
    sections_data = {}
    for item in items:
        sec_name = item.section_name
        if sec_name not in sections_data:
            sections_data[sec_name] = []
        sections_data[sec_name].append(item)

    context = {
        "inspection": inspection,
        "sections_data": sections_data,
    }
    return render(request, "checklists/inspection_readonly.html", context)


@admin_required
def admin_weekly_schedule(request):
    log = logger.bind(user_id=request.user.id)

    log.info("fetching_schedule_data_start - Старт построения расписания")

    today = timezone.now().date()
    start_of_current_week = today - timedelta(days=today.weekday())

    # 1. Загружаем МАРШРУТЫ
    routes = (
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )

    # --- ИЗМЕНЕНИЕ 1: Загружаем расписание на 4 недели (28 дней) ---
    end_date = start_of_current_week + timedelta(days=28)

    all_schedules = Schedule.objects.filter(
        date__range=[start_of_current_week, end_date]
    ).select_related("inspector", "inspection", "template")

    log.info(
        "db_data_loaded - Данные загружены с БД",
        routes_count=len(routes),
        schedules_count=len(all_schedules),
    )

    # 3. Создаем карту маршрутов: {template_id: route_id}
    tmpl_to_route = {}
    for r in routes:
        for t in r.templates.all():
            tmpl_to_route[t.id] = r.id

    # 4. Группируем Schedule по (route_id, date)
    # Теперь значением в словаре будет СПИСОК объектов Schedule
    schedule_map = {}
    for item in all_schedules:
        route_id = tmpl_to_route.get(item.template_id)
        if route_id:
            key = (route_id, item.date)
            if key not in schedule_map:
                schedule_map[key] = []
            schedule_map[key].append(item)

    # 5. Генерируем данные для 3-х недель
    weeks_data = []

    # --- ИЗМЕНЕНИЕ 2: Цикл на 4 недели ---
    for i in range(4):  # 0, 1, 2, 3
        week_start = start_of_current_week + timedelta(weeks=i)
        # week_days = [week_start + timedelta(days=d) for d in range(5)]

        week_days = [week_start + timedelta(days=d) for d in range(6)]

        rows = []
        for route in routes:
            cells = []
            for day in week_days:
                # Получаем СПИСОК всех заданий для этого маршрута в этот день
                day_schedules = schedule_map.get((route.id, day))

                if day_schedules:
                    # Анализируем статус всей пачки
                    total_count = len(day_schedules)

                    # Считаем, сколько завершено
                    completed_count = sum(
                        1
                        for s in day_schedules
                        if s.inspection and s.inspection.is_completed
                    )

                    # Статус маршрута:
                    # True - если завершены ВСЕ отчеты маршрута
                    # False - если есть хоть один недоделанный
                    is_fully_completed = completed_count == total_count

                    # Берем инспектора (он у всех задач в этот день один и тот же)
                    inspector = day_schedules[0].inspector
                    is_swapped = day_schedules[0].is_swapped

                    # Кладем в ячейку умный объект
                    cells.append(
                        {
                            "inspector": inspector,
                            "is_fully_completed": is_fully_completed,
                            "is_swapped": is_swapped,
                            "total": total_count,
                            "completed": completed_count,
                            # Берем первую задачу просто чтобы передать её ID в модалку обмена
                            "first_schedule_id": day_schedules[0].id,
                            "date": day,
                        }
                    )
                else:
                    cells.append(None)

            rows.append({"route": route, "cells": cells})

        # --- ИЗМЕНЕНИЕ 3: Название новой вкладки ---
        # Индексы: 0(Текущая), 1(Следующая), 2(Через 2 недели), 3(Через 3 недели)
        if i == 0:
            title = "Текущая"
        elif i == 1:
            title = "Следующая"
        elif i == 2:
            title = "Через 2 недели"
        else:
            title = "Через 3 недели"

        weeks_data.append(
            {
                "index": i,
                "title": title,
                "date_start": week_days[0],
                "date_end": week_days[-1],
                "week_days": week_days,
                "table_rows": rows,
            }
        )

    context = {
        "weeks_data": weeks_data,
        "today": today,
    }

    # В конце обработки:
    log.info(
        "fetching_schedule_data_completed - Данные построены для отображения таблицы"
    )
    return render(request, "checklists/admin_schedule.html", context)


@admin_required
@require_POST
def admin_exchange_shifts(request):
    is_silent = request.POST.get("is_silent") == "true"

    # 1. Получаем ID конкретной смены, на которую кликнули
    source_schedule_id = request.POST.get("source_schedule_id")

    log = logger.bind(
        requestor_id=request.user.id,
        source_schedule_id=source_schedule_id,
        is_silent=is_silent,
    )
    log.info("swap_request_received - Старт замены проверяющих")

    # 2. Получаем ИСХОДНУЮ смену и вычисляем её маршрут
    source_schedule = get_object_or_404(Schedule, id=source_schedule_id)
    source_date_str = str(source_schedule.date)
    source_user = source_schedule.inspector

    source_route = source_schedule.template.routes.first()
    if not source_route:
        messages.error(
            request, "Ошибка: Шаблон смены не привязан ни к одному маршруту."
        )
        return redirect("admin_schedule")

    # 3. Получаем ЦЕЛЕВУЮ смену (с кем меняемся)
    target_value = request.POST.get("target_schedule_id")  # "2025-12-15|5"

    if not target_value:
        messages.error(request, "Неполные данные для обмена.")
        return redirect("admin_schedule")

    try:
        target_date_str, target_inspector_id = target_value.split("|")
    except ValueError:
        messages.error(request, "Ошибка формата целевой смены.")
        return redirect("admin_schedule")

    # 4. ИЩЕМ ЗАДАЧИ
    route_templates = source_route.templates.all()

    # ИСХОДНЫЕ ЗАДАЧИ (Кто отдает): Строго задачи того маршрута, на который кликнул админ
    source_tasks = list(
        Schedule.objects.filter(
            date=source_date_str,
            inspector=source_user,
            template__in=route_templates,  # <--- ФИЛЬТР ОСТАВЛЯЕМ ТОЛЬКО ЗДЕСЬ
        )
    )

    # ЦЕЛЕВЫЕ ЗАДАЧИ (Кто принимает): Забираем ВСЕ задачи кандидата на этот день,
    # независимо от того, на каких маршрутах он должен был работать!
    target_tasks = list(
        Schedule.objects.filter(
            date=target_date_str,
            inspector_id=target_inspector_id,
            # <--- УБРАЛИ ФИЛЬТР ПО МАРШРУТУ ЗДЕСЬ!
        )
    )

    if not source_tasks or not target_tasks:
        messages.error(
            request,
            "Не удалось найти задачи для обмена (возможно они уже удалены или выполнены).",
        )
        return redirect("admin_schedule")

    target_user = target_tasks[0].inspector

    try:
        # 5. СОВЕРШАЕМ ОБМЕН (Cross-Route Swap)
        with transaction.atomic():
            log.info(
                "swap_processing - Процесс смены проверяющих",
                source_route=source_route.title,
                source_tasks_count=len(source_tasks),
                target_tasks_count=len(target_tasks),
            )

            # Исходные задачи (Сборка) отдаем Целевому юзеру
            for t in source_tasks:
                t.inspector = target_user
                t.is_swapped = (
                    False  # Целевой теперь работает сегодня, может отказаться
                )
                t.save()

            # Целевые задачи (например, ЭМО из будущего) отдаем Исходному юзеру
            for t in target_tasks:
                t.inspector = source_user
                t.is_swapped = True  # Исходный уехал в будущее, его больше не трогать
                t.save()

            # Формируем причину для лога БД
            reason_text = f"Обмен сменами: {source_user.last_name} {source_user.first_name} (с маршрута «{source_route.title}» за {source_date_str}) <-> {target_user.last_name} {target_user.first_name} (за {target_date_str})"
            if is_silent:
                reason_text += " [Тихая замена]"

            # Пишем лог
            SwapLog.objects.create(
                requestor=request.user,
                target_user=target_user,
                source_date=source_date_str,
                target_date=target_date_str,
                reason=reason_text,
            )

            # 6. ЛОГИКА УВЕДОМЛЕНИЙ
            if not is_silent:
                notify_user_about_swap.delay(source_date_str, target_user.id)
                notify_user_about_swap.delay(target_date_str, source_user.id)
                log.info("swap_notification_queued")
            else:
                log.info("swap_notification_skipped")

        messages.success(
            request,
            f"Успешный обмен: {target_user.last_name} выходит {source_date_str} на «{source_route.title}», а {source_user.last_name} — {target_date_str} на смену {target_user.last_name}.",
        )
    except Exception as e:
        log.error("swap_failed - Ошибка при обмене", error=str(e), exc_info=True)
        messages.error(request, "Ошибка при обмене")

    return redirect("admin_schedule")


@admin_required
def admin_employees_list(request):
    """
    Список всех сотрудников с поиском, фильтрацией и статистикой.
    """
    query = request.GET.get("q", "")

    # 1. БАЗОВЫЙ ЗАПРОС
    users = User.objects.all().order_by("last_name", "first_name")

    if query:
        users = users.filter(
            Q(last_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(email__icontains=query)
        )

    # 2. СТАТИСТИКА (Считаем только активных)
    # Используем .count() - это быстро, так как делается на стороне БД (SELECT COUNT(*))
    active_users = User.objects.filter(is_active=True)

    total_active = active_users.count()

    count_workers = active_users.filter(role=User.ROLE_WORKER).count()

    count_auditors = active_users.filter(can_perform_inspections=True).count()

    count_admins = active_users.filter(role=User.ROLE_ADMIN).count()

    # Менеджмент - это все остальные
    # Исключаем 'worker' и 'admin'
    count_management = active_users.exclude(
        role__in=[User.ROLE_WORKER, User.ROLE_ADMIN]
    ).count()

    context = {
        "users": users,
        "search_query": query,
        # Передаем статистику в шаблон
        "total_active": total_active,
        "count_workers": count_workers,
        "count_auditors": count_auditors,
        "count_management": count_management,
        "count_admins": count_admins,
    }
    return render(request, "checklists/admin_employees.html", context)


@admin_required
@require_POST
def admin_generate_schedule_view(request):
    """
    Ручной запуск генератора.
    Логика: Заполнить остаток текущей недели (ВКЛЮЧАЯ СЕГОДНЯ) + N полных следующих недель.
    """
    weeks_to_add = int(request.POST.get("weeks", 1))

    today = timezone.now().date()

    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Было: start_date = today + timedelta(days=1)
    start_date = today  # Начинаем прямо с СЕГОДНЯШНЕГО дня
    # -----------------------

    # 1. Находим Воскресенье ТЕКУЩЕЙ недели
    days_until_sunday = 6 - today.weekday()
    current_sunday = today + timedelta(days=days_until_sunday)

    # 2. Находим Воскресенье ЦЕЛЕВОЙ недели
    target_end_date = current_sunday + timedelta(weeks=weeks_to_add)

    # 3. Считаем количество дней
    days_count = (target_end_date - start_date).days + 1

    if days_count <= 0:
        # Это может случиться только если weeks=0 и сегодня воскресенье, но у нас weeks минимум 1
        messages.warning(request, "Нечего генерировать.")
        return redirect("admin_schedule")

    result_message = generate_schedule(start_date, days_count=days_count)

    messages.success(
        request,
        f"Генератор обработал период с {start_date.strftime('%d.%m')} "
        f"по {target_end_date.strftime('%d.%m')}. {result_message}",
    )
    return redirect("admin_schedule")


@admin_required
def admin_swap_log(request):
    """
    Журнал замен смен с фильтрацией и пагинацией.
    """
    # 1. Базовый запрос
    queryset = SwapLog.objects.select_related("requestor", "target_user").order_by(
        "-created_at"
    )

    # 2. ФИЛЬТРАЦИЯ (Считываем параметры из URL)
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    search_query = request.GET.get("q", "")

    # Применяем фильтры
    if date_from:
        # Фильтруем по дате создания записи (когда произошла замена)
        queryset = queryset.filter(created_at__date__gte=date_from)

    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)

    if search_query:
        # Ищем по ФИО инициатора или ФИО жертвы
        queryset = queryset.filter(
            Q(requestor__last_name__icontains=search_query)
            | Q(requestor__first_name__icontains=search_query)
            | Q(target_user__last_name__icontains=search_query)
            | Q(target_user__first_name__icontains=search_query)
        )

    # 3. ПАГИНАЦИЯ (20 записей на страницу)
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,  # Передаем объект страницы, а не весь queryset
        # Возвращаем параметры фильтра обратно в шаблон, чтобы заполнить инпуты
        "filter_date_from": date_from,
        "filter_date_to": date_to,
        "search_query": search_query,
    }
    return render(request, "checklists/admin_swaps.html", context)


@admin_required
def admin_analytics_dashboard(request):
    """
    Сводная таблица (Pivot) за выбранный месяц.
    Учитывает праздники РБ и генерирует года динамически.
    """
    # 1. ГОДА И МЕСЯЦЫ
    today = timezone.now().date()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    # 1. Придумываем уникальное имя для кэша (Например: "analytics_2026_05")
    cache_key = f"analytics_{year}_{month:02d}"

    # 2. Пытаемся взять готовый словарь из Redis
    context = cache.get(cache_key)

    # 3. Если кэша нет (или его удалили) — считаем заново
    if not context:
        context = analytics_dashboard(today, year, month)

        # Кэшируем текущий месяц на 24 часа, а прошлые месяцы — на 30 дней.
        # (Нам не страшно ставить большое время, ведь мы будем удалять кэш при изменениях!)
        if year < today.year or (year == today.year and month < today.month):
            timeout = 60 * 60 * 24 * 30  # 30 дней
        else:
            timeout = 60 * 60 * 24  # 24 часа

        # Кладем в Redis
        cache.set(cache_key, context, timeout)

    return render(request, "checklists/admin_analytics.html", context)


@admin_required
def admin_violations_report_page(request):
    """
    Страница для просмотра детализированного отчета по нарушениям.
    """
    # Передаем участки для селекта
    locations = Location.objects.all().order_by("name")

    # Дефолтные даты для полей ввода (чтобы они не были пустыми визуально)
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)

    context = {
        "locations": locations,
        "default_start": week_ago.strftime("%Y-%m-%d"),
        "default_end": today.strftime("%Y-%m-%d"),
    }
    return render(request, "checklists/management_violations_report.html", context)


@admin_required
def admin_inspection_edit_view(request, inspection_id):
    """Страница редактирования завершенного отчета администратором"""
    inspection = get_object_or_404(Inspection, id=inspection_id, is_completed=True)

    if request.method == "POST":
        # 1. Пробегаемся по всем пунктам и обновляем данные из формы
        for item in inspection.items.all():
            # Получаем статус (true = ОК, false = Нарушение)
            status_val = request.POST.get(f"status_{item.id}")
            if status_val in ["true", "false"]:
                item.is_compliant = status_val == "true"

            # Получаем комментарий
            comment_val = request.POST.get(f"comment_{item.id}")
            if comment_val is not None:
                item.comment = comment_val.strip()

            # Получаем чекбокс "Повторное нарушение"
            # Если чекбокс не нажат, в POST его не будет, поэтому проверяем наличие
            is_repeated = request.POST.get(f"repeated_{item.id}") == "on"
            item.is_repeated_violation = is_repeated

            item.save()

        # 2. Магия: Запускаем Celery-таску на пересчет баллов
        # Так как архитектура идемпотентна, таска удалит старые баллы разделов и создаст новые
        task_calculate_score.delay(inspection.id)

        messages.success(
            request,
            f"Отчет #{inspection.id} успешно изменен. Баллы отправлены на пересчет в фоне.",
        )
        return redirect("admin_history")

    # --- ЛОГИКА ОТОБРАЖЕНИЯ (GET) ---
    items = list(inspection.items.select_related("criteria_origin__section").all())
    items.sort(
        key=lambda i: (
            i.criteria_origin.section.order
            if i.criteria_origin and i.criteria_origin.section
            else 9999,
            i.criteria_order,
        )
    )

    # Группируем по разделам для шаблона
    sections_data = {}
    for item in items:
        sec_name = item.section_name
        if sec_name not in sections_data:
            sections_data[sec_name] = []
        sections_data[sec_name].append(item)

    context = {
        "inspection": inspection,
        "sections_data": sections_data,
    }
    return render(request, "checklists/admin_inspection_edit.html", context)
