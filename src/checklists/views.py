from django.db.models import Count, Q
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from checklists.models import (
    ChecklistTemplate,
    Inspection,
    ViolationPhoto,
    InspectionItem,
)
from checklists.decorators import admin_required, employee_required
from checklists.services import create_inspection_from_template


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


# --- ЗОНА СОТРУДНИКА (Строгий режим) ---
@employee_required
def employee_dashboard(request):
    user = request.user

    # ФИЛЬТРАЦИЯ ДАННЫХ:
    # Сотрудник видит ТОЛЬКО свои проверки.
    # Даже если он подменит ID в URL, он не увидит чужое (это реализуем ниже).

    available_templates = ChecklistTemplate.objects.all()
    my_inspections = Inspection.objects.filter(inspector=user).order_by("-created_at")[
        :5
    ]

    context = {
        "available_templates": available_templates,
        "my_inspections": my_inspections,
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

    # Нам нужно сгруппировать пункты по секциям, чтобы красиво вывести в HTML.
    # Django шаблоны не умеют хорошо группировать сами, поэтому поможем им.

    # 1. Получаем все пункты, отсортированные по порядку
    items = inspection.items.select_related("criteria_origin").order_by(
        "section_name", "criteria_order"
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

    inspection = create_inspection_from_template(
        template=template,
        user=request.user,
        date=timezone.now().date(),
        location_snapshot=template.location.name,
    )
    return redirect("inspection_form", inspection_id=inspection.id)


# --- ГЛАВНЫЙ ВХОД (Диспетчер) ---
@login_required
def index_dispatcher(request):
    """
    Единственное место, где мы решаем, кого куда послать при входе.
    """
    if request.user.role in ["admin", "master"] or request.user.is_staff:
        return redirect("admin_dashboard")
    elif request.user.role == "worker":
        return redirect("employee_dashboard")
    else:
        # Если роль не задана - кидаем на страницу входа или 403
        return redirect("users:login")


@employee_required
@require_POST
def upload_photo_ajax(request, item_id):
    """
    Принимает фото через AJAX, сохраняет и возвращает JSON с URL картинки.
    """
    # 1. Ищем пункт проверки (и проверяем, что это отчет текущего юзера)
    item = get_object_or_404(
        InspectionItem, id=item_id, inspection__inspector=request.user
    )

    # 2. Получаем файлы
    photos = request.FILES.getlist("photos")
    data = []

    for photo in photos:
        vp = ViolationPhoto.objects.create(item=item, image=photo)
        data.append({"id": vp.id, "url": vp.image.url})

    # 3. Возвращаем список загруженных фото
    return JsonResponse({"status": "ok", "photos": data})


@employee_required
@require_POST
def delete_photo_ajax(request, photo_id):
    """
    Удаляет конкретное фото по ID.
    """
    # Ищем фото, но обязательно проверяем, что оно принадлежит отчету текущего юзера!
    photo = get_object_or_404(
        ViolationPhoto, id=photo_id, item__inspection__inspector=request.user
    )

    photo.delete()

    return JsonResponse({"status": "ok"})


@employee_required
@require_POST
def save_comment_ajax(request, item_id):
    item = get_object_or_404(
        InspectionItem, id=item_id, inspection__inspector=request.user
    )
    item.comment = request.POST.get("comment", "")
    # Если написали коммент, логично переключить статус на False (Нарушение),
    # но лучше оставить это на совести пользователя или UI.
    item.save()
    return JsonResponse({"status": "ok"})
