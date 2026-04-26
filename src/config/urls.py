from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include
from django.shortcuts import render

from django.conf import settings
from django.conf.urls.static import static

import structlog

logger = structlog.get_logger(__name__)


def custom_404(request, exception=None):
    logger.warning(
        "page_not_found",
        path=request.path,
        referer=request.META.get("HTTP_REFERER", ""),
    )
    return render(request, "404.html", status=404)


def custom_500(request):
    logger.error("internal_server_error_handler")
    return render(request, "500.html", status=500)


def health_check(request):
    return HttpResponse("ok")


handler404 = custom_404
handler500 = custom_500

urlpatterns = [
    path("api/v1/health", health_check),
    path("auth/", include("users.urls", namespace="users")),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    # МЕТРИКИ (Ставим ПЕРЕД catch-all маршрутами)
    path("", include("django_prometheus.urls")),
    path("", include("checklists.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
