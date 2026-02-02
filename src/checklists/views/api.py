from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.shortcuts import get_object_or_404

from checklists.models import (
    ViolationPhoto,
    InspectionItem,
    Schedule,
)
from checklists.decorators import employee_required, admin_required

User = get_user_model()


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


@employee_required
@require_POST
def save_status_ajax(request, item_id):
    """
    Автосохранение галочки (ОК/Нарушение).
    """
    item = get_object_or_404(
        InspectionItem, id=item_id, inspection__inspector=request.user
    )

    # Получаем значение 'true' или 'false'
    status_str = request.POST.get("is_compliant")

    # Преобразуем в булево
    if status_str == "true":
        item.is_compliant = True
    elif status_str == "false":
        item.is_compliant = False

    item.save()
    return JsonResponse({"status": "ok"})


@admin_required
@require_POST
def toggle_employee_permission(request, user_id):
    """
    Переключает право 'can_perform_inspections' у сотрудника.
    """
    user = get_object_or_404(User, id=user_id)

    # Меняем на противоположное
    user.can_perform_inspections = not user.can_perform_inspections
    user.save()

    return JsonResponse({"status": "ok", "new_state": user.can_perform_inspections})


@admin_required
@require_GET
def api_get_swap_candidates(request):
    """
    API: Возвращает список смен, доступных для обмена,
    которые идут СТРОГО ПОЗЖЕ переданной даты.
    """
    source_date_str = request.GET.get("date")  # Ожидаем формат YYYY-MM-DD

    if not source_date_str:
        return JsonResponse({"error": "Date is required"}, status=400)

    # Ищем смены позже указанной даты
    candidates = (
        Schedule.objects.filter(
            date__gt=source_date_str,  # Строго больше даты исходной смены
            inspection__isnull=True,  # Еще не выполнено
            is_swapped=False,  # Еще не менялись
        )
        .select_related("inspector", "template__location")
        .order_by("date", "inspector__last_name")
    )

    data = []
    for item in candidates:
        # Формируем красивую строку для селекта
        label = f"📅 {item.date.strftime('%d.%m')} — {item.inspector.last_name} {item.inspector.first_name}"
        # label = f"📅 {item.date.strftime('%d.%m')} — {item.inspector.last_name} {item.inspector.first_name}. ({item.template.location.name[:20]})"
        data.append({"id": item.id, "label": label})

    return JsonResponse({"candidates": data})
