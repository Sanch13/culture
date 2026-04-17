{container=~"web-culture|celery-culture"} | json
| line_format "[{{upper .level}}] {{.event}} {{if or .duration .duration_sec}}(dur: {{or .duration .duration_sec}}s){{end}} » {{if .request}}{{.request}}{{end}}{{if .code}} [{{.code}}]{{end}} | {{if .user_id}}u:{{.user_id}}{{end}} {{if .task_name}}task:{{.task_name}}{{end}} | {{.logger}} | {{.trace_id}}"


{container=~"web-culture|celery-culture"}
| json
| line_format " [{{.level}}] | {{.logger}} | {{if or .duration .duration_sec}}(dur: {{or .duration .duration_sec}}s){{end}} | event: {{.event}} | {{.email}} | view_name:{{.view_name}} | user:{{.user_id}} | ip:{{.ip}} | trace:{{.trace_id}}"

u:{{or .user_id "system"}}

{container=~"web|celery"} | json
| line_format `[{{upper .level}}] [{{upper .component}}] {{.event}} {{if .duration}}dur: {{.duration}}s{{end}} » {{if .request}}{{.request}}{{end}}{{if .code}} [{{.code}}]{{end}} | u:{{or .user_id "system"}} | {{if .email}}email: {{.email}}{{end}} | {{if .task_name}}task:{{.task_name}}{{end}} | {{.logger}} | {{.trace_id}}`


### search by id
```sql
{container=~"web|celery"} | json
|= ""
| line_format `[{{upper .level}}] {{.event}} {{if .duration}} dur: {{.duration}}s {{end}} » {{if .request}}{{.request}}{{end}}{{if .code}} [{{.code}}]{{end}} u:{{or .user_id "system"}} {{if .task_name}}task:{{.task_name}}{{end}} | {{.logger}} | {{.trace_id}}`
```



### пример
```shell
2026-04-16 13:53:07.419 [INFO] email_delivery_finished  »  u:system  | checklists.tasks | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.054 [INFO] email_delivery_started  »  u:system  | checklists.tasks | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.053 [INFO] request_finished  dur: 0.0237s  » GET /cabinet/schedule/ [200] u:1  | django_structlog.middlewares.request | 965d2bfe7475669024dc646a150e648f
2026-04-16 13:53:07.052 [INFO] notification_preparation_completed  »  u:48 task:checklists.tasks.notify_user_about_swap | checklists.tasks | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.052	[INFO] task_enqueued  »  u:system  | django_structlog.celery.receivers | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.046	[INFO] dispatching_send_email_task  »  u:48 task:checklists.tasks.notify_user_about_swap | checklists.tasks | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.039	[INFO] fetching_schedule_data_completed  »  u:1  | checklists.views.admin_cabinet | 965d2bfe7475669024dc646a150e648f
2026-04-16 13:53:07.039	[INFO] db_data_loaded  »  u:1  | checklists.views.admin_cabinet | 965d2bfe7475669024dc646a150e648f
2026-04-16 13:53:07.029	[INFO] fetching_schedule_data_start  »  u:1  | checklists.views.admin_cabinet | 965d2bfe7475669024dc646a150e648f
2026-04-16 13:53:07.029	[INFO] request_started  » GET /cabinet/schedule/ u:1  | django_structlog.middlewares.request | 965d2bfe7475669024dc646a150e648f
2026-04-16 13:53:07.010	[INFO] request_finished  dur: 0.2373s  » POST /cabinet/admin/exchange-shifts/ [302] u:1  | django_structlog.middlewares.request | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.009	[INFO] swap_successful  »  u:1  | checklists.views.admin_cabinet | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.009	[INFO] preparing_swap_notification  »  u:48 task:checklists.tasks.notify_user_about_swap | checklists.tasks | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.007	[INFO] swap_notification_queued  »  u:1  | checklists.views.admin_cabinet | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:07.007	[INFO] task_enqueued  »  u:1  | django_structlog.celery.receivers | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:06.779	[INFO] swap_processing  »  u:1  | checklists.views.admin_cabinet | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:06.773	[INFO] swap_request_received  »  u:1  | checklists.views.admin_cabinet | 25de8a818f93dd3116417eba27f6bafe
2026-04-16 13:53:06.773	[INFO] request_started  » POST /cabinet/admin/exchange-shifts/ u:1  | django_structlog.middlewares.request | 25de8a818f93dd3116417eba27f6bafe
```
