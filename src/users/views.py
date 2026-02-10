from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib import messages

from checklists.decorators import employee_required
from users.forms import CustomUserCreationForm, UserProfileForm
from users.tasks import notify_admins_about_registration


def register(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            # 1. Сохраняем пользователя в БД
            user = form.save()
            # 2. Сразу логиним его (чтобы не заставлять вводить пароль снова)
            login(request, user)
            # 3. --- ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ АДМИНАМ (В ФОНЕ) ---
            # transaction.on_commit не обязателен, если не используются сложные транзакции,
            # но хорошая практика вызывать celery после сохранения в БД.
            notify_admins_about_registration.delay(user.id)
            # 4. Перенаправляем на главную (или в кабинет)
            return redirect("index")
    else:
        form = CustomUserCreationForm()

    return render(request, "users/register.html", {"form": form})


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
