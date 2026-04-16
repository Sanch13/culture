import structlog
from opentelemetry import trace
from django.dispatch import receiver

from django_structlog.signals import bind_extra_request_metadata


def add_otel_trace_id(logger, log_method, event_dict):
    """Добавляет trace_id в каждый лог, чтобы связать Grafana Loki и Tempo"""
    try:
        span = trace.get_current_span()
        ctx = span.get_span_context()

        # Добавляем trace_id всегда, если контекст валиден
        if ctx.is_valid:
            event_dict["trace_id"] = trace.format_trace_id(ctx.trace_id)
            event_dict["span_id"] = trace.format_span_id(ctx.span_id)
            # Опционально: флаг, был ли спан записан
            event_dict["trace_sampled"] = ctx.trace_flags.sampled
    except Exception:
        # Если OTel не инициализирован — не ломаем логирование
        pass
    return event_dict


def add_component_name(logger, method_name, event_dict):
    """Определяет короткое имя компонента на основе имени логгера или задачи"""
    logger_name = event_dict.get("logger", "")

    # 1. Если есть task_id или task_name — это точно Celery
    if "task_id" in event_dict or "task_name" in event_dict:
        event_dict["component"] = "CELERY"

    # 2. Если логгер начинается на checklists
    elif logger_name.startswith("checklists"):
        event_dict["component"] = "CHECKLIST"

    # 3. Если логгер начинается на users
    elif logger_name.startswith("users"):
        event_dict["component"] = "USERS"

    # 4. Если это системные логи Django
    elif logger_name.startswith("django"):
        event_dict["component"] = "DJANGO"

    # 5. Всё остальное
    else:
        event_dict["component"] = "SYSTEM"

    return event_dict


@receiver(bind_extra_request_metadata)
def bind_user_info(request, logger, **kwargs):
    # Контекст запроса
    structlog.contextvars.bind_contextvars(
        ip=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT"),
    )

    if hasattr(request, "user") and request.user.is_authenticated:
        structlog.contextvars.bind_contextvars(
            user_id=request.user.id, email=request.user.email
        )
    else:
        structlog.contextvars.bind_contextvars(user_id="anonymous")


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        add_otel_trace_id,
        add_component_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json_formatter": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
            "foreign_pre_chain": [
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                add_otel_trace_id,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.stdlib.ExtraAdder(),
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json_formatter",
        },
    },
    # ROOT перехватит ВСЕ логи от любых библиотек
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,  # Выключаем проброс в root, чтобы лог не двоился
        },
        "django_structlog": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "checklists": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "users": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
