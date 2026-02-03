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
from checklists.services import generate_schedule
from checklists.models import (
    ChecklistTemplate,
    Inspection,
    Schedule,
    SwapLog,
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
    # Берем только завершенные, сортируем: свежие сверху.
    # select_related ускоряет загрузку (подтягивает юзера и шаблон сразу)
    inspections = (
        Inspection.objects.filter(is_completed=True)
        .select_related("inspector", "template")
        .annotate(
            # Считаем количество пунктов, где is_compliant = False
            violation_count=Count("items", filter=Q(items__is_compliant=False))
        )
        .order_by("-date_check", "-created_at")
    )

    context = {"inspections": inspections}
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
    start_of_current_week = today - timedelta(
        days=today.weekday()
    )  # Понедельник текущей недели

    # Загружаем шаблоны (они одинаковы для всех недель)
    templates = ChecklistTemplate.objects.all().order_by("id")

    # Загружаем ВСЕ расписание на 3 недели вперед одним запросом (оптимизация)
    # 3 недели * 7 дней = 21 день
    end_date = start_of_current_week + timedelta(days=21)

    all_schedules = Schedule.objects.filter(
        date__range=[start_of_current_week, end_date]
    ).select_related("inspector", "inspection")

    # Создаем карту для быстрого поиска: {(template_id, date): schedule_item}
    schedule_map = {(item.template_id, item.date): item for item in all_schedules}

    # Генерируем данные для 3-х недель
    weeks_data = []

    for i in range(3):  # 0, 1, 2
        # Начало конкретной недели (сдвиг на i * 7 дней)
        week_start = start_of_current_week + timedelta(weeks=i)

        # Генерируем дни Пн-Пт для этой недели
        week_days = [week_start + timedelta(days=d) for d in range(5)]

        # Собираем строки таблицы для этой недели
        rows = []
        for tmpl in templates:
            cells = []
            for day in week_days:
                # Берем из общей карты
                cells.append(schedule_map.get((tmpl.id, day)))

            rows.append({"template": tmpl, "cells": cells})

        # Добавляем неделю в общий список
        weeks_data.append(
            {
                "index": i,  # Для ID вкладок (0, 1, 2)
                "title": "Текущая"
                if i == 0
                else ("Следующая" if i == 1 else "Через 2 недели"),
                "date_start": week_days[0],
                "date_end": week_days[-1],
                "week_days": week_days,
                "table_rows": rows,
            }
        )
    # Нам нужны смены, начиная с ЗАВТРАШНЕГО дня, которые еще не выполнены.
    # Мы будем предлагать их для обмена.
    future_schedules = (
        Schedule.objects.filter(
            date__gt=today,  # Строго больше сегодня
            inspection__isnull=True,  # Отчет еще не создан (не выполнено)
            is_swapped=False,  # Исключаем тех, кто уже менялся (по твоему ТЗ)
        )
        .select_related("inspector", "template__location")
        .order_by("date", "inspector__last_name")
    )

    context = {
        "weeks_data": weeks_data,
        "today": today,
        "future_schedules": future_schedules,
    }
    return render(request, "checklists/admin_schedule.html", context)


@admin_required
@require_POST
def admin_exchange_shifts(request):
    # ID текущей смены (которую меняем)
    current_schedule_id = request.POST.get("current_schedule_id")
    # ID будущей смены (на которую меняем)
    target_schedule_id = request.POST.get("target_schedule_id")

    # Получаем обе записи
    current_sched = get_object_or_404(Schedule, id=current_schedule_id)
    target_sched = get_object_or_404(Schedule, id=target_schedule_id)

    # 3. Совершаем обмен
    with transaction.atomic():
        current_user = current_sched.inspector  # Тот, кто был сегодня
        target_user = target_sched.inspector  # Тот, кто был завтра (донор)

        current_date = current_sched.date
        target_date = target_sched.date

        # === ЛОГИКА ОБМЕНА (SWAP) ===
        # 1. Меняем инспекторов местами
        current_sched.inspector = target_user
        current_sched.is_swapped = False

        target_sched.inspector = current_user
        target_sched.is_swapped = True

        # 2. Сохраняем
        current_sched.save()
        target_sched.save()

        # 3. Пишем лог (Один общий или два)
        SwapLog.objects.create(
            requestor=request.user,  # Админ
            target_user=target_user,  # Кого поставили сегодня
            source_date=current_date,
            target_date=target_date,
            reason=f"Обмен сменами с {current_user.last_name} ({current_date}) <-> {target_user.last_name} ({target_date})",
        )

        # 4. Уведомляем ОБОИХ сотрудников
        # user_b теперь работает сегодня -> шлем ему письмо
        notify_user_about_swap.delay(current_sched.id)

        # user_a теперь работает завтра -> шлем ему письмо про завтра
        # notify_user_about_swap.delay(target_sched.id)
        messages.success(
            request,
            f"Успешно: {target_user.last_name} выходит, {current_user.last_name} перенесен на {target_sched.date}.",
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
