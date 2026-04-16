import time
import structlog


class StructlogDurationMiddleware:
    """
    Middleware для вычисления длительности запроса и добавления её в structlog.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Засекаем время перед тем, как запрос уйдет дальше
        start_time = time.perf_counter()

        # 2. Передаем запрос дальше (во вьюхи и другие мидлвари)
        response = self.get_response(request)

        # 3. Когда запрос вернулся, считаем разницу
        duration = time.perf_counter() - start_time

        # 4. Кладем результат в "глобальный мешок" контекста
        structlog.contextvars.bind_contextvars(duration=round(duration, 4))

        return response
