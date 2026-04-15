from config.base import *  # noqa: F403


DEBUG = False
ALLOWED_HOSTS = settings.ALLOWED_HOSTS_FOR_DEPLOY  # noqa: F405
STATIC_ROOT = BASE_DIR / "static/"  # noqa: F405

CSRF_TRUSTED_ORIGINS = [
    settings.SITE_URL,  # noqa: F405
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

if DEBUG:
    SITE_URL = "http://127.0.0.1:8000"
else:
    SITE_URL = settings.SITE_URL  # noqa: F405
