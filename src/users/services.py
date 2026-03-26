import requests
from django.conf import settings
from .models import AllowedCorporateEmail


def sync_mailcow_emails():
    """
    Стучится в API Mailcow по заданному URL, получает все активные почтовые ящики
    домена и обновляет локальную таблицу AllowedCorporateEmail.
    """
    # Берем полный URL прямо из настроек
    api_url = getattr(settings, "MAILCOW_API_URL", None)
    api_key = getattr(settings, "MAILCOW_API_KEY", None)

    if not api_url or not api_key:
        return "Ошибка: Не настроены MAILCOW_API_URL или MAILCOW_API_KEY в .env"

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    try:
        # Делаем GET запрос по полному URL
        response = requests.get(api_url, headers=headers, timeout=10)

        # Проверяем, что сервер вернул 200 OK
        response.raise_for_status()

        mailboxes = response.json()

        # Mailcow API возвращает список словарей.
        # Фильтруем только активные ящики (active == 1) и берем их username (email)
        active_emails = [
            box["username"].lower() for box in mailboxes if box.get("active") == 1
        ]

        if not active_emails:
            return "API вернул пустой список или нет активных ящиков."

        # --- СИНХРОНИЗАЦИЯ С БАЗОЙ ДАННЫХ ---

        # 1. Получаем email-ы, которые УЖЕ ЕСТЬ в нашей таблице AllowedCorporateEmail
        existing_emails = set(
            AllowedCorporateEmail.objects.values_list("email", flat=True)
        )

        # Множество email-ов, полученных из Mailcow
        mailcow_emails_set = set(active_emails)

        # 2. Находим, кого нужно ДОБАВИТЬ (Есть в Mailcow, но нет у нас)
        emails_to_add = mailcow_emails_set - existing_emails

        # 3. Находим, кого нужно УДАЛИТЬ (Есть у нас, но удален/заблокирован в Mailcow)
        emails_to_remove = existing_emails - mailcow_emails_set

        # Выполняем изменения в БД (оптимизированными bulk-запросами)
        if emails_to_add:
            AllowedCorporateEmail.objects.bulk_create(
                [AllowedCorporateEmail(email=email) for email in emails_to_add]
            )

        if emails_to_remove:
            AllowedCorporateEmail.objects.filter(email__in=emails_to_remove).delete()

        return f"Синхронизация успешна. Добавлено: {len(emails_to_add)}. Удалено: {len(emails_to_remove)}."

    except requests.exceptions.RequestException as e:
        return f"Ошибка сети при обращении к Mailcow API: {e}"
    except ValueError:
        return "Ошибка: API вернул не JSON формат."
    except Exception as e:
        return f"Критическая ошибка синхронизации: {e}"
