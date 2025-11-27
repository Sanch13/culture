import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.dev")

app = Celery("culture")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# CELERY_BEAT_SCHEDULE = {
# 	'send_admin_email': {
# 		'task': 'apps.core.tasks.send_admin_email',
# 		'schedule': crontab(hour=9, minute=25, day_of_week='mon-fri'),
# 	},
# }
