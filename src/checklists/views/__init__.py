from checklists.views.dispatcher import index_dispatcher
from checklists.views.admin_cabinet import (
    admin_dashboard,
    admin_templates,
    template_preview,
    admin_inspection_list,
    admin_inspection_detail,
    admin_weekly_schedule,
    admin_employees_list,
    admin_generate_schedule_view,
    admin_swap_log,
    admin_exchange_shifts,
)
from checklists.views.employee_cabinet import (
    employee_dashboard,
    inspection_form_view,
    start_inspection_view,
    auto_swap_shift,
)
from checklists.views.api import (
    upload_photo_ajax,
    delete_photo_ajax,
    save_comment_ajax,
    save_status_ajax,
    toggle_employee_permission,
    api_get_swap_candidates,
)

__all__ = (
    "index_dispatcher",
    "admin_dashboard",
    "admin_templates",
    "template_preview",
    "admin_inspection_list",
    "admin_inspection_detail",
    "admin_weekly_schedule",
    "admin_employees_list",
    "admin_generate_schedule_view",
    "admin_swap_log",
    "admin_exchange_shifts",
    "employee_dashboard",
    "inspection_form_view",
    "start_inspection_view",
    "auto_swap_shift",
    "upload_photo_ajax",
    "delete_photo_ajax",
    "save_comment_ajax",
    "save_status_ajax",
    "toggle_employee_permission",
    "api_get_swap_candidates",
)
