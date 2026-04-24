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
    path("cabinet/schedule/", views.admin_weekly_schedule, name="admin_schedule"),
    path("cabinet/employees/", views.admin_employees_list, name="admin_employees"),
    path(
        "cabinet/generate-schedule/",
        views.admin_generate_schedule_view,
        name="admin_generate_schedule",
    ),
    path("cabinet/swaps/", views.admin_swap_log, name="admin_swaps"),
    path(
        "cabinet/admin/exchange-shifts/",
        views.admin_exchange_shifts,
        name="admin_exchange_shifts",
    ),
    path("cabinet/analytics/", views.admin_analytics_dashboard, name="admin_analytics"),
    path(
        "cabinet/management/violations/",
        views.admin_violations_report_page,
        name="admin_management_violations",
    ),
    # =====================
    # Предпросмотр конкретного шаблона
    # =====================
    path("preview/<int:template_id>/", views.template_preview, name="template_preview"),
    # =====================
    # Кабинет пользователя
    # =====================
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
        "management/reports/", views.management_reports_list, name="management_reports"
    ),
    path(
        "management/report/<int:inspection_id>/",
        views.management_inspection_detail,
        name="management_report_detail",
    ),
    path(
        "management/analytics/",
        views.management_analytics_dashboard,
        name="management_analytics",
    ),
    path(
        "management/violations/",
        views.management_violations_report_page,
        name="management_violations_report_page",
    ),
    # =====================
    # API
    # =====================
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
    path(
        "api/save-status/<int:item_id>/",
        views.save_status_ajax,
        name="save_status_ajax",
    ),
    path(
        "api/toggle-permission/<int:user_id>/",
        views.toggle_employee_permission,
        name="toggle_employee_permission",
    ),
    path("api/swap/<int:schedule_id>/", views.auto_swap_shift, name="auto_swap_shift"),
    path(
        "api/admin/swap-candidates/",
        views.api_get_swap_candidates,
        name="api_get_swap_candidates",
    ),
    path(
        "api/save-repeated/<int:item_id>/",
        views.save_repeated_ajax,
        name="save_repeated_ajax",
    ),
    path(
        "api/analytics/violations/",
        views.api_get_violations_report,
        name="api_violations_report",
    ),
]
