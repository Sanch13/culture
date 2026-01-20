from datetime import timedelta

from django.shortcuts import render, get_object_or_404
from django.db.models import Count, Q
from django.contrib.auth import get_user_model
from django.utils import timezone

from checklists.decorators import admin_required
from checklists.models import (
    ChecklistTemplate,
    Inspection,
    Schedule,
)

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
    """
    Матрица расписания: Строки - Шаблоны, Колонки - Дни недели (Пн-Пт).
    """
    today = timezone.now().date()

    # 1. Вычисляем даты Пн-Пт текущей недели
    # today.weekday(): 0=Пн ... 6=Вс
    start_of_week = today - timedelta(days=today.weekday())  # Понедельник

    # Генерируем список из 5 дней (Пн, Вт, Ср, Чт, Пт)
    week_days = [start_of_week + timedelta(days=i) for i in range(5)]

    # 2. Получаем данные
    templates = ChecklistTemplate.objects.all().order_by("id")

    # Загружаем расписание только на эти 5 дней
    schedules = Schedule.objects.filter(
        date__range=[week_days[0], week_days[-1]]
    ).select_related("inspector", "inspection")

    # 3. Превращаем список расписания в словарь для быстрого поиска
    # Ключ: (template_id, date) -> Значение: schedule_object
    schedule_map = {}
    for item in schedules:
        schedule_map[(item.template_id, item.date)] = item

    # 4. Собираем структуру для таблицы
    table_rows = []

    for tmpl in templates:
        row = {"template": tmpl, "cells": []}

        # Для каждого дня недели ищем, есть ли запись для этого шаблона
        for day in week_days:
            # Ищем в словаре
            cell_data = schedule_map.get((tmpl.id, day))
            row["cells"].append(cell_data)  # Добавляем объект Schedule или None

        table_rows.append(row)

    context = {
        "week_days": week_days,  # Заголовки колонок
        "table_rows": table_rows,  # Тело таблицы
        "today": today,
    }
    return render(request, "checklists/admin_schedule.html", context)


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
