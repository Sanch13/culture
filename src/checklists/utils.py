import sys
import ssl
from io import BytesIO
import smtplib
from email.message import Message

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile

from PIL import Image, ImageOps
import phonenumbers
import pillow_heif

pillow_heif.register_heif_opener()


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

    # logger.info(f"Письмо успешно отправлено!")


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


def compress_image(image, quality=80, max_width=1280):
    output = BytesIO()

    with Image.open(image) as img:
        # --- 1. МАГИЯ EXIF (Ориентация) ---
        # Эта функция читает EXIF тег Orientation и физически вращает изображение (на 90, 180, 270 градусов).
        # Если тега нет (или это скриншот/android) — она ничего не делает и работает быстро.
        try:
            img = ImageOps.exif_transpose(img)
        except Exception as e:
            # На случай странных или поврежденных EXIF-данных просто игнорируем ошибку
            print(f"Ошибка при чтении EXIF: {e}")

        # --- 2. Конвертация цвета ---
        if img.mode in ("RGBA", "LA"):
            background = Image.new(img.mode[:-1], img.size, "#fff")
            background.paste(img, img.split()[-1])
            img_to_save = background
        elif img.mode != "RGB":
            img_to_save = img.convert("RGB")
        else:
            img_to_save = img.copy()

        # --- 3. Ресайз ---
        width, height = img_to_save.size
        if width > max_width:
            ratio = max_width / width
            new_height = int(height * ratio)
            img_to_save = img_to_save.resize(
                (max_width, new_height), Image.Resampling.LANCZOS
            )

        # --- 4. Сохранение ---
        img_to_save.save(output, format="WEBP", quality=quality, optimize=True)

    output.seek(0)

    # Безопасное формирование имени (замена спецсимволов)
    # Чтобы избежать ошибок, если имя файла содержит пробелы или странные символы
    safe_name = image.name.split("/")[-1].split("\\")[-1]
    new_name = safe_name.rsplit(".", 1)[0] + ".webp"

    return InMemoryUploadedFile(
        output, "ImageField", new_name, "image/webp", sys.getsizeof(output), None
    )
