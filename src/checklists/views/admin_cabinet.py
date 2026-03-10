from datetime import timedelta

from django.shortcuts import render, get_object_or_404
from django.db.models import Count, Q
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from checklists.decorators import admin_required
from checklists.services import generate_schedule, apply_inspection_filters_and_paginate
from checklists.models import (
    ChecklistTemplate,
    Inspection,
    Schedule,
    SwapLog,
    InspectionRoute,
)
from checklists.tasks import notify_user_about_swap

User = get_user_model()


# --- ЗОНА АДМИНИСТРАТОРА (Строгий режим) ---
@admin_required
def admin_dashboard(request):
    total_templates = ChecklistTemplate.objects.count()
    context = {"total_templates": total_templates}
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
    Просмотр конкретного отчета (Read-Only).
    """
    # Ищем отчет по ID (без фильтра по юзеру, т.к. админ может смотреть чужое)
    inspection = get_object_or_404(Inspection, id=inspection_id)

    # Та же логика группировки, что и при заполнении
    items = inspection.items.prefetch_related("photos").order_by(
        "section_name", "criteria_order"
    )

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
    today = timezone.now().date()
    start_of_current_week = today - timedelta(days=today.weekday())

    # 1. Загружаем МАРШРУТЫ
    routes = (
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )

    # 2. Загружаем расписание на 3 недели
    end_date = start_of_current_week + timedelta(days=21)

    all_schedules = Schedule.objects.filter(
        date__range=[start_of_current_week, end_date]
    ).select_related("inspector", "inspection", "template")

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

    for i in range(3):
        week_start = start_of_current_week + timedelta(weeks=i)
        week_days = [week_start + timedelta(days=d) for d in range(5)]

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

        weeks_data.append(
            {
                "index": i,
                "title": "Текущая"
                if i == 0
                else ("Следующая" if i == 1 else "Через 2 недели"),
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
    return render(request, "checklists/admin_schedule.html", context)


@admin_required
@require_POST
def admin_exchange_shifts(request):
    # 1. Получаем данные ИСХОДНОЙ смены (кто отдает)
    source_date_str = request.POST.get("source_date")
    source_inspector_id = request.POST.get("source_inspector_id")

    # 2. Получаем данные ЦЕЛЕВОЙ смены (кто принимает)
    target_value = request.POST.get("target_schedule_id")  # "2025-12-15|5"

    if not all([source_date_str, source_inspector_id, target_value]):
        messages.error(request, "Неполные данные для обмена.")
        return redirect("admin_schedule")

    try:
        target_date_str, target_inspector_id = target_value.split("|")
    except ValueError:
        messages.error(request, "Ошибка формата целевой смены.")
        return redirect("admin_schedule")

    # 3. ИЩЕМ ЗАДАЧИ
    source_tasks = list(
        Schedule.objects.filter(date=source_date_str, inspector_id=source_inspector_id)
    )
    target_tasks = list(
        Schedule.objects.filter(date=target_date_str, inspector_id=target_inspector_id)
    )

    if not source_tasks or not target_tasks:
        messages.error(
            request,
            "Не удалось найти задачи для обмена (возможно они уже удалены или выполнены).",
        )
        return redirect("admin_schedule")

    # 4. СОВЕРШАЕМ ОБМЕН (Batch Swap)
    with transaction.atomic():
        # Получаем объекты пользователей для логов и уведомлений
        source_user = source_tasks[0].inspector
        target_user = target_tasks[0].inspector

        # Меняем Исходных (отдаем целевому)
        for t in source_tasks:
            t.inspector = target_user
            t.is_swapped = False  # Целевой теперь работает сегодня
            t.save()

        # Меняем Целевых (отдаем исходному)
        for t in target_tasks:
            t.inspector = source_user
            t.is_swapped = True  # Исходный уехал в будущее, его больше не трогать
            t.save()

        # Пишем лог
        SwapLog.objects.create(
            requestor=request.user,
            target_user=target_user,
            source_date=source_date_str,
            target_date=target_date_str,
            reason=f"Обмен сменами {source_user.last_name} ({source_date_str}) <-> {target_user.last_name} ({target_date_str})",
        )

        # Уведомления (если нужно)
        # target_user теперь назначен на source_date
        notify_user_about_swap.delay(source_date_str, target_user.id)
        # source_user теперь назначен на target_date
        # notify_user_about_swap.delay(target_date_str, source_user.id)

    messages.success(
        request,
        f"Успешный обмен: {target_user.last_name} выходит {source_date_str}, а {source_user.last_name} — {target_date_str}.",
    )
    return redirect("admin_schedule")


@admin_required
def admin_employees_list(request):
    """
    Список всех сотрудников с поиском и фильтрацией.
    """
    query = request.GET.get("q", "")  # Получаем поисковый запрос из URL (?q=Иванов)

    # Базовый запрос: Все пользователи, сортируем по Фамилии
    users = User.objects.all().order_by("last_name", "first_name")

    # Если есть поиск - фильтруем
    if query:
        users = users.filter(
            Q(last_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(email__icontains=query)
        )

    context = {
        "users": users,
        "search_query": query,
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
    Журнал замен смен.
    """
    # Сортируем: новые сверху (-created_at)
    swaps = SwapLog.objects.select_related("requestor", "target_user").order_by(
        "-created_at"
    )

    context = {
        "swaps": swaps,
    }
    return render(request, "checklists/admin_swaps.html", context)
