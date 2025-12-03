from django.urls import path
from . import views

urlpatterns = [
    # Кабинет администратора
    path("cabinet/", views.admin_cabinet, name="admin_cabinet"),
    # Предпросмотр конкретного шаблона
    path("preview/<int:template_id>/", views.template_preview, name="template_preview"),
]
