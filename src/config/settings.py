from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    SECRET_KEY: SecretStr
    DEBUG: bool
    ALLOWED_HOSTS: list
    ALLOWED_HOSTS_FOR_DEPLOY: list

    EMAIL_HOST: str
    EMAIL_PORT: int
    EMAIL_USE_SSL: bool
    EMAIL_USE_TLS: bool
    EMAIL_HOST_USER: str
    EMAIL_HOST_PASSWORD: SecretStr
    DEFAULT_FROM_EMAIL: str

    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr
    POSTGRES_DB: str
    POSTGRES_DB_HOST: str
    POSTGRES_DB_PORT: int

    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    CELERY_ACCEPT_CONTENT: list
    CELERY_TASK_SERIALIZER: str

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
