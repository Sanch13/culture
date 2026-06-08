import sys
import ssl
from io import BytesIO
import smtplib
from email.message import Message

from django.conf import settings
from django.db.models import Avg
from django.core.files.uploadedfile import InMemoryUploadedFile

import pyvips
import phonenumbers
import structlog

from checklists.models import LocationDailyScore, Inspection, InspectionSectionScore

logger = structlog.get_logger(__name__)


def is_privileged_user(user):
    """
    Возвращает True, если пользователю разрешен доступ в админ-панель.
    Это Админы, Мастера или Staff.
    """
    return user.is_staff or user.role in ["admin", "master"]


def send_message(message: Message):
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT) as server:
        server.starttls(context=context)
        server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
        server.send_message(message)


def format_phone_number(raw_phone):
    """
    Принимает строку (например, '375291234567')
    Возвращает красиво (например, '+375 29 123-45-67')
    Если ошибка - возвращает исходную строку.
    """
    if not raw_phone:
        return ""

    # Приводим к строке на всякий случай
    s_phone = str(raw_phone)

    # Если в начале нет плюса, добавляем его.
    # Библиотека лучше работает, когда номер начинается с +
    if not s_phone.startswith("+"):
        s_phone = "+" + s_phone

    try:
        # Парсим номер
        parsed_num = phonenumbers.parse(s_phone, None)

        # Проверяем валидность (опционально, но полезно)
        if not phonenumbers.is_valid_number(parsed_num):
            return raw_phone

        # Форматируем в международный формат
        return phonenumbers.format_number(
            parsed_num, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except phonenumbers.NumberParseException:
        # Если пришел мусор, возвращаем как есть, чтобы ничего не упало
        return raw_phone


def get_data_information_about_department(department) -> tuple[str, str]:
    if department.manager:
        m_phone = format_phone_number(department.manager.phone) or "нет телефона"
        manager_text = f"{department.manager.get_full_name()} (Тел: {m_phone})"
    else:
        manager_text = "Не назначен"

    masters_list = []
    for master in department.masters.all():
        m_phone = format_phone_number(master.phone) or "нет телефона"
        masters_list.append(f"- {master.get_full_name()} (Тел: {m_phone})")

    masters_block = (
        "\n".join(masters_list) if masters_list else "Нет назначенных мастеров"
    )
    return manager_text, masters_block


def compress_image(image_file, quality=80, max_width=1280):
    """
    Сжатие и автоповорот изображения с помощью сверхбыстрой библиотеки pyvips.
    """
    log = logger.bind(
        original_name=image_file.name,
        target_quality=quality,
        target_max_width=max_width,
    )
    try:
        log.info(
            "start_image_compression - Старт сжатия изображения",
            original_size_bytes=image_file.size,
        )
        # 1. Читаем картинку из переданного файла (Django InMemoryUploadedFile)
        # image_file.read() возвращает байты.
        image_bytes = image_file.read()

        # Загружаем из буфера памяти.
        # pyvips автоматически определит формат (JPEG, PNG, WEBP, HEIC)
        img = pyvips.Image.new_from_buffer(image_bytes, "")

        # 2. АВТОПОВОРОТ (EXIF Orientation)
        # Эта команда мгновенно разворачивает фото так, как телефон его снял.
        img = img.autorot()

        # 3. РЕСАЙЗ
        # pyvips делает это очень элегантно. Если ширина больше max_width, мы вычисляем коэффициент.
        if img.width > max_width:
            scale = max_width / img.width
            log.info(
                "resizing_image - Изменение размера изображения",
                original_width=img.width,
                new_width=max_width,
                scale_factor=f"{scale:.2f}",
            )
            # Функция resize изменяет размер с интерполяцией (по умолчанию Lanczos3)
            img = img.resize(scale)

        # 4. СОХРАНЕНИЕ В WEBP
        # pyvips может писать результат напрямую в строку (байты)
        # Q=quality (качество). У WebP по умолчанию lossless=False, что нам и нужно.
        buffer_out = img.write_to_buffer(".webp", Q=quality)

        # 5. Возвращаем объект для Django
        output = BytesIO(buffer_out)

        log.info("compression_completed - Сжатие изображения успешно завершено")

        # Безопасное формирование имени (убираем старое расширение, ставим .webp)
        safe_name = image_file.name.split("/")[-1].split("\\")[-1]
        new_name = safe_name.rsplit(".", 1)[0] + ".webp"

        return InMemoryUploadedFile(
            output, "ImageField", new_name, "image/webp", sys.getsizeof(output), None
        )

    except pyvips.Error as e:
        log.error(
            "pyvips_processing_error - Ошибка обработки изображения",
            error_type=type(e).__name__,
            error_message=str(e),
            stage="image_processing",
        )
        raise Exception(f"Ошибка обработки изображения (pyvips): {str(e)}")

    except Exception as e:
        log.exception(
            "unexpected_compression_error - Непредвиденная ошибка при сжатии",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        raise Exception(f"Непредвиденная ошибка при сжатии: {str(e)}")


def get_previous_month_averages(year, month):
    """
    Высчитывает средние баллы за предыдущий месяц для:
    1. Участков (loc_avg_map)
    2. Шаблонов (tmpl_avg_map)
    3. Глобального B1 (b1_prev_avg)
    """
    # Определяем предыдущий месяц и год
    if month == 1:
        prev_month = 12
        prev_year = year - 1
    else:
        prev_month = month - 1
        prev_year = year

    # 1. Участки (LocationDailyScore)
    loc_qs = LocationDailyScore.objects.filter(
        date__year=prev_year, date__month=prev_month
    )
    loc_avg_map = {
        item["location_id"]: round(item["avg"], 2)
        for item in loc_qs.values("location_id").annotate(avg=Avg("score"))
        if item["avg"] is not None
    }

    # 2. Шаблоны (Inspection)
    tmpl_qs = Inspection.objects.filter(
        date_check__year=prev_year, date_check__month=prev_month, is_completed=True
    )
    tmpl_avg_map = {
        item["template_id"]: round(item["avg"], 2)
        for item in tmpl_qs.values("template_id").annotate(avg=Avg("final_score"))
        if item["avg"] is not None
    }

    # 3. Глобальный B1 (тут нужна твоя специфика с минимальным баллом <= 3.01)
    b1_scores = InspectionSectionScore.objects.filter(
        date_check__year=prev_year, date_check__month=prev_month, section_type="B1"
    )
    b1_day_map = {}
    for b1 in b1_scores:
        if b1.date_check not in b1_day_map:
            b1_day_map[b1.date_check] = []
        if b1.score is not None:
            b1_day_map[b1.date_check].append(b1.score)

    b1_daily_results = []
    for day, scores in b1_day_map.items():
        if scores:
            if min(scores) <= 3.01:
                b1_daily_results.append(min(scores))
            else:
                b1_daily_results.append(sum(scores) / len(scores))

    b1_prev_avg = None
    if b1_daily_results:
        b1_prev_avg = round(sum(b1_daily_results) / len(b1_daily_results), 2)

    return loc_avg_map, tmpl_avg_map, b1_prev_avg
