from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("auth/", include("users.urls")),
    path("", include("checklists.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
]
