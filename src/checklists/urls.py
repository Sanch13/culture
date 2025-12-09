from django.urls import path
from . import views

urlpatterns = [
    path("", views.index_dispatcher, name="index"),
    # Кабинет администратора
    path("cabinet/", views.admin_dashboard, name="admin_dashboard"),
    path("cabinet/templates/", views.admin_templates, name="admin_templates"),
    path("cabinet/history/", views.admin_inspection_list, name="admin_history"),
    path(
        "cabinet/report/<int:inspection_id>/",
        views.admin_inspection_detail,
        name="admin_report_detail",
    ),
    # Предпросмотр конкретного шаблона
    path("preview/<int:template_id>/", views.template_preview, name="template_preview"),
    path("my-checks/", views.employee_dashboard, name="employee_dashboard"),
    path(
        "start/<int:template_id>/", views.start_inspection_view, name="start_inspection"
    ),
    path(
        "inspection/<int:inspection_id>/",
        views.inspection_form_view,
        name="inspection_form",
    ),
    path(
        "api/upload-photo/<int:item_id>/",
        views.upload_photo_ajax,
        name="upload_photo_ajax",
    ),
    path(
        "api/delete-photo/<int:photo_id>/",
        views.delete_photo_ajax,
        name="delete_photo_ajax",
    ),
    path(
        "api/save-comment/<int:item_id>/",
        views.save_comment_ajax,
        name="save_comment_ajax",
    ),
]
