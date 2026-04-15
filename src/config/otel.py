from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision

from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor

from config.settings import settings

CLEAN_EXCLUDED_PATHS = [
    "/metrics",
    "/admin/jsi18n/",
    "/favicon.ico",
    "/api/v1/health",
]


class ExcludeMetricsSampler(Sampler):
    """
    Отбрасывает трейсы, у которых URL совпадает с /metrics или /favicon.ico.
    """

    def __init__(self, excluded_paths: list[str], ratio: float | None = None):
        # Базовый сэмплер (100% или доля)
        root_sampler = TraceIdRatioBased(ratio) if ratio else TraceIdRatioBased(1.0)
        # Оборачиваем его в ParentBased (уважать решение родителя)
        self.base_sampler = ParentBased(root=root_sampler)
        self.excluded_paths = excluded_paths

    def should_sample(
        self,
        parent_context,
        trace_id,
        name,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
        **kwargs,
    ) -> SamplingResult:
        # OTel обычно сохраняет URL в атрибуте 'http.target' или 'url.path'
        if attributes:
            target = attributes.get("http.target", "")
            url = attributes.get(
                "url.path", ""
            )  # В свежих версиях OTel используется url.path

            # Проверяем, есть ли наш target в списке исключений
            for excluded_path in self.excluded_paths:
                if (target and excluded_path in target) or (
                    url and excluded_path in url
                ):
                    return SamplingResult(Decision.DROP)

        return self.base_sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return (
            f"ExcludeEndpointsSampler(excluded={self.excluded_paths}, "
            f"base={self.base_sampler.get_description()})"
        )


def setup_opentelemetry():
    # Деталь 1: Resource (Кто мы такие?)
    # Мы говорим: "Я сервис culture, версия 1.0"
    resource = Resource.create(
        attributes={
            "service.name": "culture",
        }
    )

    # Деталь 2: TracerProvider (Главный менеджер)
    # Инициализируем провайдер с нашим сэмплером
    # Это "фабрика", которая будет создавать спаны. Мы отдаем ей наш Resource.
    provider = TracerProvider(
        resource=resource,
        sampler=ExcludeMetricsSampler(excluded_paths=CLEAN_EXCLUDED_PATHS, ratio=0.5),
    )

    # Деталь 3: Exporter + Processor (Куда отправлять и как)
    # Exporter: Умеет отправлять данные (по HTTP или gRPC в формате OTLP).
    # Настраиваем курьера (Exporter) - он повезет данные в Tempo
    otlp_exporter = OTLPSpanExporter(
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        timeout=settings.OTEL_EXPORTER_OTLP_TIMEOUT,
    )
    # Processor: Умеет копить спаны в пачки (Batch) и отправлять их раз в секунду,
    # чтобы не делать 1000 мелких сетевых запросов и не тормозить Django.
    # Настраиваем упаковщика (Processor) - он соберет 50 спанов в одну коробку
    processor = BatchSpanProcessor(otlp_exporter)
    # Отдаем упаковщика Главному менеджеру
    provider.add_span_processor(processor)

    # Мы говорим всему Python-коду: "Теперь это главный провайдер по умолчанию"
    trace.set_tracer_provider(provider)

    # Деталь 4: Instrumentors (Авто-шпионы)
    # (ловит HTTP запросы)
    DjangoInstrumentor().instrument(
        is_sql_commentor_enabled=True,
    )
    # (ловит SQL запросы)
    Psycopg2Instrumentor().instrument()
    RequestsInstrumentor().instrument()
    CeleryInstrumentor().instrument()
