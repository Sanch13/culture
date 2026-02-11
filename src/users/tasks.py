from celery import shared_task
from django.contrib.auth import get_user_model

from checklists.tasks import send_email
from checklists.utils import format_phone_number

User = get_user_model()


@shared_task
def notify_admins_about_registration(new_user_id):
    """
    Находит всех админов и шлет им уведомление о новом пользователе.
    """
    User = get_user_model()

    try:
        # 1. Получаем данные нового пользователя
        new_user = User.objects.get(id=new_user_id)

        # 2. Формируем текст письма
        subject = "👤 Новый пользователь в системе"
        body = (
            f"Зарегистрировался новый сотрудник:\n\n"
            f"ФИО: {new_user.first_name} {new_user.last_name}\n"
            f"Email: {new_user.email}\n"
            f"Телефон: {format_phone_number(new_user.phone) if new_user.phone else 'Не указан'}\n\n"
            f"Пожалуйста, зайдите в админку, чтобы выдать ему права (допуск к проверкам)."
        )

        # 3. Находим всех админов (role='admin' или is_superuser=True)
        # Или просто filter(role=User.ROLE_ADMIN), если ты используешь только роли.
        admins = User.objects.filter(role=User.ROLE_ADMIN, is_active=True)

        # Если админов нет - выходим
        if not admins.exists():
            return "Админы не найдены"

        # 4. Рассылаем каждому админу
        for admin in admins:
            if admin.email:
                # Вызываем твою задачу отправки
                send_email.delay(to=admin.email, subject=subject, body=body)

        return f"Уведомления отправлены {admins.count()} администраторам."

    except User.DoesNotExist:
        return "Ошибка: Пользователь не найден"
