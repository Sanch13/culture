from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from checklists.context_processors import BOSS_ROLES
from checklists.models import (
    ChecklistTemplate,
    Inspection,
    ViolationPhoto,
    Schedule,
)
from checklists.decorators import employee_required
from checklists.services import (
    create_inspection_from_template,
    perform_auto_swap,
    apply_inspection_filters_and_paginate,
    is_global_viewer,
)
from checklists.tasks import notify_user_about_swap, notify_admin_about_swap

User = get_user_model()


# --- ЗОНА СОТРУДНИКА (Строгий режим) ---
@employee_required
def employee_dashboard(request):
    user = request.user
    today = timezone.now().date()

    # Праздники (если используешь)
    # by_holidays = holidays.BY()
    # holiday_name = by_holidays.get(today)

    # 1. Вычисляем конец недели
    days_until_sunday = 6 - today.weekday()
    end_of_week = today + timedelta(days=days_until_sunday)

    # 2. Ищем ВСЕ задания на этой неделе
    # Убираем .first(), берем весь список .all() (или просто без метода, т.к. это QuerySet)
    week_schedule = (
        Schedule.objects.filter(inspector=user, date__range=[today, end_of_week])
        .select_related("template", "inspection")
        .order_by("date")
    )

    # 3. Разделяем логику: "Сегодня" vs "Будущее"

    # Список задач СТРОГО на сегодня
    today_tasks = [t for t in week_schedule if t.date == today]

    # ПРОВЕРКА ДЛЯ КНОПКИ АВТОЗАМЕНЫ
    # Можно ли сегодня меняться? (Только если ни один отчет еще не начат)
    can_swap_today = False
    if today_tasks:
        can_swap_today = not any(t.inspection for t in today_tasks)

    # Ближайшая будущая задача (для отображения "Ждите до четверга")
    # Берем первую задачу, дата которой больше сегодня
    future_task = None
    days_until = 0

    if not today_tasks:
        # Если сегодня пусто, ищем, когда следующая смена
        for t in week_schedule:
            if t.date > today:
                future_task = t
                days_until = (t.date - today).days
                break  # Нашли ближайшую, выходим

    # 4. История
    my_inspections = Inspection.objects.filter(inspector=user).order_by("-created_at")[
        :5
    ]

    context = {
        "today_tasks": today_tasks,  # СПИСОК (может быть пустым)
        "can_swap_today": can_swap_today,
        "future_task": future_task,  # ОДИН объект (или None)
        "days_until": days_until,
        "my_inspections": my_inspections,
        # "holiday_name": holiday_name,
    }
    return render(request, "checklists/employee_dashboard.html", context)


@employee_required
def inspection_form_view(request, inspection_id):
    # 1. Безопасность: Получаем отчет только если он принадлежит текущему юзеру
    inspection = get_object_or_404(Inspection, id=inspection_id, inspector=request.user)

    # Если отчет уже завершен - перекидываем на страницу просмотра (read-only),
    # чтобы случайно не отредактировали. (Её сделаем позже, пока просто редирект)
    if inspection.is_completed:
        # messages.info(request, "Этот отчет уже отправлен в архив.")
        return redirect("employee_dashboard")

    if request.method == "POST":
        # --- ЛОГИКА СОХРАНЕНИЯ ---

        # 1. Определяем, какую кнопку нажали: "Сохранить черновик" или "Завершить"
        action = request.POST.get("action")

        # Пробегаемся по всем пунктам этого отчета
        for item in inspection.items.all():
            # Формируем имена полей, которые мы ждем от HTML
            # Например: "compliant_15" (где 15 - id пункта)
            status_key = f"compliant_{item.id}"
            comment_key = f"comment_{item.id}"
            photos_key = f"photos_{item.id}"

            # Получаем данные из формы
            # Если ключа нет в POST, значит галочку не трогали (оставляем как есть)
            if status_key in request.POST:
                # Превращаем строку 'true'/'false' в Python Boolean
                item.is_compliant = request.POST.get(status_key) == "true"
            if comment_key in request.POST:
                item.comment = request.POST.get(comment_key)
            item.save()

            files = request.FILES.getlist(photos_key)

            for file in files:
                # Создаем запись в таблице ViolationPhoto
                ViolationPhoto.objects.create(item=item, image=file)

        # 2. Если нажали "Завершить проверку"
        if action == "complete":
            # Тут можно добавить валидацию: все ли поля заполнены?
            inspection.is_completed = True
            inspection.save()
            # messages.success(request, "Проверка успешно завершена и отправлена!")
            return redirect("employee_dashboard")

        else:
            # Если просто "Сохранить"
            # messages.success(request, "Черновик сохранен.")
            return redirect("inspection_form", inspection_id=inspection.id)

    # --- ЛОГИКА ОТОБРАЖЕНИЯ (GET) ---

    # 1. Получаем все пункты, отсортированные по порядку
    items = list(inspection.items.select_related("criteria_origin__section").all())

    # 2. Сортируем средствами Python
    # Ключ сортировки:
    # 1. criteria_origin.section.order (Порядок оригинального раздела: 0, 1, 2...)
    # 2. criteria_order (Порядок вопроса внутри раздела)
    # Если оригинал удален (criteria_origin is None), кидаем его в самый конец (9999)
    items.sort(
        key=lambda i: (
            i.criteria_origin.section.order
            if i.criteria_origin and i.criteria_origin.section
            else 9999,
            i.criteria_order,
        )
    )

    # 2. --- ПОИСК ИСТОРИИ (НОВОЕ) ---
    # Ищем ПОСЛЕДНЮЮ завершенную проверку по ЭТОМУ же шаблону, но СТАРШЕ текущей
    last_inspection = (
        Inspection.objects.filter(
            template=inspection.template,
            is_completed=True,
            date_check__lt=inspection.date_check,  # Строго до текущей даты
        )
        .order_by("-date_check", "-created_at")
        .first()
    )

    # Словарь для быстрого поиска: { id_критерия: объект_прошлого_нарушения }
    history_map = {}

    if last_inspection:
        # Берем только пункты с нарушениями из прошлого отчета
        bad_items = last_inspection.items.filter(is_compliant=False).prefetch_related(
            "photos"
        )
        for bad_item in bad_items:
            # criteria_origin_id - это ссылка на "Родительский вопрос"
            # Мы используем его как ключ, чтобы сопоставить "Вчерашний вопрос" и "Сегодняшний вопрос"
            if bad_item.criteria_origin_id:
                history_map[bad_item.criteria_origin_id] = bad_item

    # 3. Приклеиваем историю к текущим пунктам
    # Мы не сохраняем это в БД, просто добавляем атрибут "на лету" для шаблона
    for item in items:
        # Если у этого вопроса есть "оригинал" и этот "оригинал" был в списке нарушений
        if item.criteria_origin_id in history_map:
            item.history = history_map[item.criteria_origin_id]

    # Группируем вручную: { "Раздел А": [Item1, Item2], "Раздел Б": [Item3] }
    sections_data = {}
    for item in items:
        sec_name = item.section_name
        if sec_name not in sections_data:
            sections_data[sec_name] = []
        sections_data[sec_name].append(item)

    context = {
        "inspection": inspection,
        "sections_data": sections_data,
        "last_inspection_date": last_inspection.date_check
        if last_inspection
        else None,  # Для заголовка
    }
    return render(request, "checklists/inspection_form.html", context)


@employee_required
@require_POST
def start_inspection_view(request, template_id):
    template = get_object_or_404(ChecklistTemplate, pk=template_id)
    today = timezone.now().date()
    user = request.user

    # --- БЛОК 0: ИЩЕМ ЗАПИСЬ В РАСПИСАНИИ (НОВОЕ) ---
    # Нам нужно знать, какую именно строчку в плане мы сейчас выполняем
    schedule_item = Schedule.objects.filter(
        inspector=user, date=today, template=template
    ).first()

    # Если этого задания нет в плане — выкидываем (строгий режим)
    if not schedule_item:
        # messages.error(request, "Ошибка: Вам не назначена эта проверка на сегодня.")
        return redirect("employee_dashboard")

    # --- БЛОК 1: ПРОВЕРКА НА СУЩЕСТВОВАНИЕ ОТЧЕТА ---
    existing_inspection = Inspection.objects.filter(
        template=template, date_check=today
    ).first()

    inspection = None  # Переменная для итогового отчета

    if existing_inspection:
        # Если отчет есть, и он МОЙ -> просто переходим в него
        if existing_inspection.inspector == user:
            inspection = existing_inspection
        # Если он ЧУЖОЙ -> Ошибка
        else:
            # messages.error(request,
            #                f"Ошибка! Эту проверку уже начал {existing_inspection.inspector.last_name}.")
            return redirect("employee_dashboard")
    else:
        # --- БЛОК 2: СОЗДАНИЕ НОВОГО ОТЧЕТА ---
        try:
            inspection = create_inspection_from_template(
                template=template,
                user=user,
                date=today,
                location_snapshot=template.location.name,
            )
        except Exception:
            # Если база не дала создать (дубль или ошибка)
            # messages.error(request, "Не удалось создать проверку. Попробуйте еще раз.")
            return redirect("employee_dashboard")

    # --- БЛОК 3: ПРИВЯЗКА К РАСПИСАНИЮ (САМОЕ ВАЖНОЕ!) ---
    # Мы говорим расписанию: "Смотри, вот отчет по твоему заданию"
    if schedule_item and schedule_item.inspection != inspection:
        schedule_item.inspection = inspection
        schedule_item.save()
        print(
            f"DEBUG: Отчет {inspection.id} успешно привязан к расписанию {schedule_item.id}"
        )

    return redirect("inspection_form", inspection_id=inspection.id)


@employee_required
@require_POST
def auto_swap_shift(request, schedule_id):
    schedule_item = get_object_or_404(Schedule, id=schedule_id, inspector=request.user)

    if schedule_item.inspection:
        messages.error(request, "Нельзя отказаться от начатого задания.")
        return redirect("employee_dashboard")

    # Получаем причину из формы
    reason = request.POST.get("reason", "").strip()

    if not reason:
        messages.error(request, "Вы обязаны указать причину отказа!")
        return redirect("employee_dashboard")

    # Сохраняем дату для уведомлений ДО того, как объект изменится
    current_date_str = schedule_item.date.strftime("%Y-%m-%d")

    # Вызываем сервис
    success, message, info_about_change = perform_auto_swap(schedule_item, reason)

    if success:
        messages.success(request, message)

        # Нам нужно узнать ID жертвы (мы можем получить его из БД,
        # так как текущая карточка теперь принадлежит ему)
        schedule_item.refresh_from_db()  # Подтягиваем новые данные
        target_user_id = schedule_item.inspector.id

        # Шлем письмо жертве ("Выходи сегодня!")
        notify_user_about_swap.delay(current_date_str, target_user_id)
        notify_admin_about_swap.delay(info_about_change)

    else:
        messages.error(request, message)

    return redirect("employee_dashboard")


@employee_required
def management_reports_list(request):
    user = request.user

    # Если не босс — выгоняем
    if user.role not in BOSS_ROLES and not user.is_staff:
        messages.error(request, "Доступ запрещен.")
        return redirect("employee_dashboard")

    # --- 1. БАЗОВЫЙ ЗАПРОС (ACCESS CONTROL) ---
    queryset = (
        Inspection.objects.filter(is_completed=True)
        .select_related("inspector", "template", "template__location")
        .annotate(violation_count=Count("items", filter=Q(items__is_compliant=False)))
        .order_by("-date_check")
    )

    # --- ЛОГИКА ДОСТУПА ---
    if not is_global_viewer(user):
        # Если НЕ глобальный - фильтруем только ЕГО участки
        queryset = queryset.filter(
            Q(template__location__manager=user)
            | Q(template__location__deputies=user)
            | Q(template__location__senior_masters=user)
            | Q(template__location__masters=user)
        ).distinct()

    # 2. Вызываем наш сервис фильтрации
    context = apply_inspection_filters_and_paginate(request, queryset)

    return render(request, "checklists/management_history.html", context)


@employee_required
def management_inspection_detail(request, inspection_id):
    user = request.user

    # Проверка базовых прав
    if user.role not in BOSS_ROLES and not user.is_staff:
        messages.error(request, "Доступ запрещен.")
        return redirect("employee_dashboard")

    # Ищем отчет
    inspection = get_object_or_404(Inspection, id=inspection_id)

    # --- ЛОГИКА ДОСТУПА ---
    # Если это НЕ глобальный зритель (не Нач.Пр., не Гл.Инж., не босс ЭМО)
    if not is_global_viewer(user):
        loc = inspection.template.location

        # Проверяем, есть ли user в одном из списков начальников ЭТОГО location
        is_local_boss = (
            loc.manager == user
            or loc.deputies.filter(id=user.id).exists()
            or loc.senior_masters.filter(id=user.id).exists()
            or loc.masters.filter(id=user.id).exists()
        )

        if not is_local_boss:
            messages.error(request, "У вас нет прав на просмотр отчетов этого участка.")
            return redirect("management_reports")

    # --- ФОРМИРОВАНИЕ ДАННЫХ ДЛЯ ОТОБРАЖЕНИЯ ---
    items = inspection.items.prefetch_related("photos").order_by(
        "section_name", "criteria_order"
    )
    sections_data = {}
    for item in items:
        sec_name = item.section_name
        if sec_name not in sections_data:
            sections_data[sec_name] = []
        sections_data[sec_name].append(item)

    context = {"inspection": inspection, "sections_data": sections_data}

    return render(request, "checklists/management_readonly.html", context)
