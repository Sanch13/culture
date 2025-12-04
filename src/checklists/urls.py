from django.urls import path
from . import views

urlpatterns = [
    # Кабинет администратора
    path("cabinet/", views.admin_dashboard, name="admin_dashboard"),
    path("cabinet/templates/", views.admin_templates, name="admin_templates"),
    # Предпросмотр конкретного шаблона
    path("preview/<int:template_id>/", views.template_preview, name="template_preview"),
]
