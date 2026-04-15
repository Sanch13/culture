import os

from django.core.wsgi import get_wsgi_application

from config.otel import setup_opentelemetry

os.environ["DJANGO_SETTINGS_MODULE"] = "config.prod"

setup_opentelemetry()

application = get_wsgi_application()
