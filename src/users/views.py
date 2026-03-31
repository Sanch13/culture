from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth import login, get_user_model
from django.contrib import messages
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator

from checklists.decorators import employee_required
from checklists.tasks import send_email
from users.forms import CustomUserCreationForm, UserProfileForm
from users.tasks import notify_admins_about_registration

User = get_user_model()


def register(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            # 1. Создаем пользователя, но НЕ АКТИВИРУЕМ
            user = form.save(commit=False)
            user.is_active = False
            user.save()

            # 2. Генерируем токен и ID для ссылки
            # Кодируем ID в base64, чтобы в URL не светились прямые цифры
            uid = urlsafe_base64_encode(force_bytes(user.id))
            token = default_token_generator.make_token(user)

            # 3. Формируем ссылку активации
            # request.build_absolute_uri() сам подставит домен (http://127.0.0.1 или https://miran...)
            activation_link = request.build_absolute_uri(
                reverse("users:activate_email", kwargs={"uidb64": uid, "token": token})
            )

            # 4. Отправляем письмо (Можно вынести в Celery!)
            subject = "Активация аккаунта в системе Культура производства"
            message = (
                f"Здравствуйте, {user.get_full_name()}!\n\n"
                f"Вы зарегистрировались в системе контроля Культуры производства.\n"
                f"Для активации аккаунта перейдите по ссылке (действительна 3 дня):\n\n"
                f"{activation_link}\n\n"
                f"Если вы не регистрировались, проигнорируйте это письмо."
            )

            send_email.delay(to=user.email, subject=subject, body=message)

            # 5. Перенаправляем на страницу-заглушку "Проверьте почту"
            return render(
                request, "users/email_verification_sent.html", {"email": user.email}
            )

    else:
        form = CustomUserCreationForm()

    return render(request, "users/register.html", {"form": form})


def activate_email(request, uidb64, token):
    """
    Обработчик ссылки из письма.
    """
    try:
        # Расшифровываем ID
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    # Если пользователь найден и токен валиден (не просрочен и не использован дважды)
    if user is not None and default_token_generator.check_token(user, token):
        # 1. АКТИВИРУЕМ
        user.is_active = True
        user.save()

        # 2. Логиним пользователя (чтобы не заставлять вводить пароль снова)
        login(request, user)

        # 3. Уведомляем админов (Таска, которая была у тебя в старом коде)
        notify_admins_about_registration.delay(user.id)

        # 4. Пишем сообщение и кидаем в систему
        messages.success(request, "Ваш аккаунт успешно активирован! Добро пожаловать.")
        return redirect("index")

    else:
        # Если ссылка битая или просроченная
        return render(request, "users/email_verification_invalid.html")


# def register(request):
#     if request.method == "POST":
#         form = CustomUserCreationForm(request.POST)
#         if form.is_valid():
#             # 1. Сохраняем пользователя в БД
#             user = form.save()
#             # 2. Сразу логиним его (чтобы не заставлять вводить пароль снова)
#             login(request, user)
#             # 3. --- ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ АДМИНАМ (В ФОНЕ) ---
#             # transaction.on_commit не обязателен, если не используются сложные транзакции,
#             # но хорошая практика вызывать celery после сохранения в БД.
#             notify_admins_about_registration.delay(user.id)
#             # 4. Перенаправляем на главную (или в кабинет)
#             return redirect("index")
#     else:
#         form = CustomUserCreationForm()
#
#     return render(request, "users/register.html", {"form": form})


@employee_required
@login_required
def profile_edit(request):
    user = request.user

    if request.method == "POST":
        # Передаем POST данные и instance (кого обновляем)
        form = UserProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Ваш профиль успешно обновлен!")
            # Перенаправляем на ту же страницу (Pattern PRG: Post/Redirect/Get)
            return redirect("users:profile_edit")
        else:
            messages.error(request, "Пожалуйста, исправьте ошибки в форме.")
    else:
        # GET запрос - просто заполняем форму текущими данными
        form = UserProfileForm(instance=user)

    return render(request, "users/profile_edit.html", {"form": form})
