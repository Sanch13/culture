from django.contrib.auth import get_user_model
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.shortcuts import get_object_or_404

from checklists.models import (
    ViolationPhoto,
    InspectionItem,
    Schedule,
)
from checklists.utils import compress_image
from checklists.decorators import employee_required, admin_required
from checklists.tasks import send_email

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
        try:
            # Сжимаем фото перед сохранением
            compressed_photo = compress_image(photo)
            vp = ViolationPhoto.objects.create(item=item, image=compressed_photo)
            data.append({"id": vp.id, "url": vp.image.url})

        except Exception as e:
            # Если файл битый или не картинка - просто пропускаем (или логируем)
            print(f"Error compressing image: {e}")
            continue

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

    if user.can_perform_inspections:
        email = user.email
        subject = "✅ Вам открыт доступ к проверкам"
        body = (
            f"Здравствуйте, {user.first_name}!\n\n"
            f"Администратор предоставил вам доступ к проверкам.\n"
            f"Теперь вы будете включены в график инспекций и можете проводить проверки.\n\n"
            f"Войдите в личный кабинет: {settings.SITE_URL}my-checks/"
        )

        send_email.delay(to=email, subject=subject, body=body)

    return JsonResponse({"status": "ok", "new_state": user.can_perform_inspections})


@admin_required
@require_GET
def api_get_swap_candidates(request):
    source_date_str = request.GET.get("date")

    if not source_date_str:
        return JsonResponse({"error": "Date is required"}, status=400)

    # Ищем уникальные комбинации (Дата + Инспектор) в будущем
    candidates_data = (
        Schedule.objects.filter(
            date__gt=source_date_str,
            inspection__isnull=True,
            is_swapped=False,
        )
        # Группируем, чтобы избежать дублей (если у человека 2 смены в день)
        .values(
            "date", "inspector__id", "inspector__last_name", "inspector__first_name"
        )
        .distinct()
        .order_by("date", "inspector__last_name")
    )

    data = []
    for item in candidates_data:
        # value: "2025-12-15|5" (Дата | ID инспектора)
        value_key = f"{item['date']}|{item['inspector__id']}"
        label = f"📅 {item['date'].strftime('%d.%m')} — {item['inspector__last_name']} {item['inspector__first_name']}"

        data.append({"id": value_key, "label": label})

    return JsonResponse({"candidates": data})
