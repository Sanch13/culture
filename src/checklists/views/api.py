import time

from datetime import timedelta
from django.utils import timezone
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
from checklists.services.cascade_shift_schedule import cascade_shift_schedule
from checklists.utils import compress_image
from checklists.decorators import employee_required, admin_required
from checklists.services.services import format_size, get_file_extension
from checklists.services.service_analytics import get_violations_report_data
from checklists.tasks import send_email
import structlog

User = get_user_model()

logger = structlog.get_logger(__name__)


@employee_required
@require_POST
def upload_photo_ajax(request, item_id):
    MAX_UPLOAD_SIZE = 10 * 1024 * 1024
    _1Mb = 1 * 1024 * 1024

    log = logger.bind(
        view="upload_photo_ajax - Вход в обработку",
        user_id=request.user.id,
        item_id=item_id,
    )

    photos = request.FILES.getlist("photos")
    if not photos:
        log.warning("upload_no_photos - Нет фотографий к обработке")
        return JsonResponse(
            {"status": "error", "message": "Файлы не найдены"}, status=400
        )

    log.info("upload_started - Старт обработки и сохранения фото", count=len(photos))

    item = get_object_or_404(
        InspectionItem, id=item_id, inspection__inspector=request.user
    )
    data = []
    errors = []
    total_start = time.perf_counter()

    for photo in photos:
        file_log = log.bind(
            file_name=photo.name,
            file_ext=get_file_extension(photo.name),
            file_size=format_size(photo.size),
        )

        if photo.size is None or photo.size > MAX_UPLOAD_SIZE:
            file_log.warning("upload_file_too_big - Фото превышает 10 МБ.")
            errors.append(f"Файл {photo.name} превышает 10 МБ.")
            continue

        try:
            if photo.size < _1Mb:
                db_start = time.perf_counter()
                vp = ViolationPhoto.objects.create(item=item, image=photo)
                db_duration = time.perf_counter() - db_start
                file_log.info(
                    "file_processed - Фото сохранено в БД.",
                    db_duration=round(db_duration, 3),
                    size=photo.size,
                )
            else:
                # Средний файл (1-10 МБ): фронтенд не сжал. Жмем сервером.
                comp_start = time.perf_counter()
                compressed_photo = compress_image(photo)  # Наш новый pyvips конвертер
                comp_duration = time.perf_counter() - comp_start

                vp = ViolationPhoto.objects.create(item=item, image=compressed_photo)

                file_log.info(
                    "file_compressed_and_saved - Фото сжато, обработано и сохранено в БД.",
                    comp_duration=round(comp_duration, 3),
                    size=format_size(photo.size),
                )

            data.append({"id": vp.id, "url": vp.image.url})

        except Exception as e:
            # Обработка ошибки конвертации (например, если загрузили видео с расширением .jpg)
            file_log.error(
                "file_processing_failed - Ошибка обработки фотографии.",
                error=str(e),
                exc_info=True,
            )
            errors.append(
                f"Не удалось обработать файл {photo.name}. Возможно, он поврежден."
            )
            continue

    total_duration = time.perf_counter() - total_start
    log.info(
        "upload_finished - Все фото обработаны и сохранены в БД.",
        total_duration=round(total_duration, 3),
        success_count=len(data),
        error_count=len(errors),
    )

    # Возвращаем ответ. Если часть загрузилась, а часть нет - сообщаем об этом.
    response_data = {
        "status": "ok" if data else "error",
        "photos": data,
    }
    if errors:
        response_data["errors"] = errors
        if not data:
            response_data["status"] = "error"  # Если вообще ничего не загрузилось

    return JsonResponse(response_data)


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
            f"Теперь вы будете включены в график аудита и можете проводить проверки.\n\n"
            f"Войдите в личный кабинет: {settings.SITE_URL}/my-checks/"
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


@employee_required
@require_POST
def save_repeated_ajax(request, item_id):
    """
    Автосохранение галочки 'Повторное нарушение'.
    """
    item = get_object_or_404(
        InspectionItem, id=item_id, inspection__inspector=request.user
    )

    # Получаем 'true' или 'false' из JS
    is_repeated_str = request.POST.get("is_repeated")

    if is_repeated_str == "true":
        item.is_repeated_violation = True
    elif is_repeated_str == "false":
        item.is_repeated_violation = False

    item.save()
    return JsonResponse({"status": "ok"})


@employee_required
@require_GET
def api_get_violations_report(request):
    location_id = request.GET.get("location_id")  # Может быть пуст, 'all', или ID

    # Получаем даты из запроса
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    # Дефолтные значения (Последние 7 дней)
    today = timezone.now().date()

    if not end_date_str:
        end_date_str = today.strftime("%Y-%m-%d")

    if not start_date_str:
        start_date = today - timedelta(days=7)
        start_date_str = start_date.strftime("%Y-%m-%d")

    try:
        report_data = get_violations_report_data(
            start_date_str, end_date_str, location_id
        )
        return JsonResponse({"status": "ok", "report": report_data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@admin_required
@require_POST
def api_cascade_shift(request, schedule_id):
    is_silent_str = request.POST.get("is_silent", "true")
    is_silent = is_silent_str.lower() == "true"

    log = logger.bind(
        service="api_cascade_shift", schedule_id=schedule_id, is_silent=is_silent
    )
    # Находим задачу, по которой кликнул Админ
    schedule_item = get_object_or_404(Schedule, id=schedule_id)

    log.info("[API] Удаление проверяющего из расписания", schedule_item=schedule_item)

    # Запрещаем трогать прошлое
    if schedule_item.date < timezone.now().date():
        return JsonResponse(
            {"status": "error", "message": "Нельзя изменить прошлые дни."}
        )

    # Запрещаем трогать начатое
    if schedule_item.inspection:
        return JsonResponse({"status": "error", "message": "Отчет уже начат."})

    target_date = schedule_item.date
    user_to_remove = schedule_item.inspector

    # Вызываем магию
    success, message = cascade_shift_schedule(
        target_date=target_date,
        user_to_remove=user_to_remove,
        requestor=request.user,
        is_silent=is_silent,
    )

    if success:
        return JsonResponse({"status": "ok", "message": message})
    else:
        return JsonResponse({"status": "error", "message": message})


@admin_required
@require_POST
def api_delete_violation_photo(request, photo_id):
    """AJAX удаление фотографии"""
    photo = get_object_or_404(ViolationPhoto, id=photo_id)
    if photo.image:
        photo.image.delete(save=False)  # Удаляем физический файл
    photo.delete()
    return JsonResponse({"status": "ok"})
