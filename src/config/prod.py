from config.base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = settings.ALLOWED_HOSTS_FOR_DEPLOY  # noqa: F405
STATIC_ROOT = BASE_DIR / "static/"  # noqa: F405
