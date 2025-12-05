from django.shortcuts import render, redirect
from django.contrib.auth import login
from users.forms import CustomUserCreationForm


def register(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            # 1. Сохраняем пользователя в БД
            user = form.save()
            # 2. Сразу логиним его (чтобы не заставлять вводить пароль снова)
            login(request, user)
            # 3. Перенаправляем на главную (или в кабинет)
            return redirect("index")
    else:
        form = CustomUserCreationForm()

    return render(request, "users/register.html", {"form": form})
