import logging
import json
from gunicorn import glogging


class JSONLogger(glogging.Logger):
    """
    Кастомный логгер для Gunicorn, который превращает системные ошибки в JSON.
    """

    def setup(self, cfg):
        super().setup(cfg)

        # Форматтер для системных логов Gunicorn (error_log)
        # Мы имитируем структуру structlog, чтобы Loki было удобно
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            json.dumps(
                {
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "logger": "gunicorn.error",
                    "event": "%(message)s",
                    "pid": "%(process)d",
                }
            )
        )
        handler.setFormatter(formatter)

        self.error_log.handlers = [handler]
        self.error_log.setLevel(logging.INFO)


# --- Основные настройки Gunicorn ---

# 1. Сеть и процессы
bind = "0.0.0.0:8000"
# workers = multiprocessing.cpu_count() * 2 + 1
workers = 4
worker_class = "sync"
timeout = 30  # Таймаут в 30 секунд

# 2. Подключаем наш кастомный JSON логгер для системных ошибок
logger_class = JSONLogger

# 3. Настройка Access логов (логи запросов)
accesslog = "-"
access_log_format = (
    "{"
    '"timestamp":"%(t)s",'
    '"remote_ip":"%(h)s",'
    '"request":"%(r)s",'
    '"status":"%(s)s",'
    '"response_length":"%(b)s",'
    '"duration_ms":%(M)s,'
    '"user_agent":"%(a)s",'
    '"event":"gunicorn_access",'
    '"logger":"gunicorn.access"'
    "}"
)

# 4. Настройка Error логов (куда слать системные ошибки)
errorlog = "-"
loglevel = "info"

# Имя процесса в мониторинге
proc_name = "culture_gunicorn"
