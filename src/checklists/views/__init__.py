from src.checklists.views.dispatcher import index_dispatcher
from src.checklists.views.admin_cabinet import (
    admin_dashboard,
    admin_templates,
    template_preview,
    admin_inspection_list,
    admin_inspection_detail,
    admin_weekly_schedule,
    admin_employees_list,
)
from src.checklists.views.employee_cabinet import (
    employee_dashboard,
    inspection_form_view,
    start_inspection_view,
    auto_swap_shift,
)
from src.checklists.views.api import (
    upload_photo_ajax,
    delete_photo_ajax,
    save_comment_ajax,
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
    "employee_dashboard",
    "inspection_form_view",
    "start_inspection_view",
    "auto_swap_shift",
    "upload_photo_ajax",
    "delete_photo_ajax",
    "save_comment_ajax",
)
