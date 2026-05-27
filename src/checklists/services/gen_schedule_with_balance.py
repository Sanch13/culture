import datetime

from django.db import transaction
from django.contrib.auth import get_user_model

import structlog

from checklists.models import InspectionRoute, Schedule, ScheduleGeneratorState
from checklists.services.services import (
    is_working_day,
    _determine_start_index,
    _load_route_statistics,
    _get_empty_routes_for_date,
    _save_generation_results,
)

User = get_user_model()
logger = structlog.get_logger(__name__)


def generate_schedule(start_date, days_count=21):
    log = logger.bind(
        service="schedule_generator", start_date=str(start_date), days_count=days_count
    )
    log.info("generator_started - Старт генерации расписания")

    routes = list(
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )
    inspectors = list(
        User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id")
    )

    if not routes or not inspectors:
        log.error(
            "generation_failed - Ошибка: Не настроены Маршруты или нет инспекторов."
        )
        return "Ошибка: Не настроены Маршруты или нет инспекторов."

    log.info(
        "resources_loaded - Ресурсы загружены",
        routes_count=len(routes),
        inspectors_count=len(inspectors),
    )

    # =====================================================================
    # 1. ЗАГРУЖАЕМ СЧЕТЧИКИ ИЗ БД В ПАМЯТЬ
    # =====================================================================
    stats, updated_stats_tracker = _load_route_statistics()

    with transaction.atomic():
        # --- ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА (УМНАЯ ЗАКЛАДКА) ---
        # 1. Берём state с блокировкой
        state, _ = ScheduleGeneratorState.objects.select_for_update().get_or_create(
            id=1
        )
        # 2. Определяем start_index
        current_inspector_idx = _determine_start_index(state.last_user_id, inspectors)

        created_total = 0
        current_date = start_date

        # =====================================================================
        # 2. ОСНОВНОЙ ЦИКЛ ГЕНЕРАЦИИ
        # =====================================================================
        for _ in range(days_count):
            if not is_working_day(current_date):
                log.info(
                    "day_skipped - День пропущен. Не рабочий день",
                    date=str(current_date),
                    reason="not_working_day",
                )
                current_date += datetime.timedelta(days=1)
                continue

            day_log = log.bind(date=str(current_date))

            # А. Ищем свободные маршруты на сегодня
            empty_routes = _get_empty_routes_for_date(current_date, routes)
            if not empty_routes:
                current_date += datetime.timedelta(days=1)
                continue

            # Б. Выводим дежурных на плац (Строгая очередь)
            daily_inspectors = []
            for _ in range(len(empty_routes)):
                daily_inspectors.append(inspectors[current_inspector_idx])
                current_inspector_idx = (current_inspector_idx + 1) % len(inspectors)

            # В. Тасуем маршруты между дежурными
            for route in empty_routes:
                daily_inspectors.sort(
                    key=lambda insp: (stats[insp.id][route.id], insp.id)
                )
                best_inspector = daily_inspectors.pop(0)

                for tmpl in route.templates.all():
                    Schedule.objects.create(
                        date=current_date, template=tmpl, inspector=best_inspector
                    )
                    created_total += 1

                # Обновляем память
                stats[best_inspector.id][route.id] += 1
                updated_stats_tracker.add((best_inspector.id, route.id))

                day_log.info(
                    "route_assigned_smart",
                    route=route.title,
                    inspector=best_inspector.get_full_name(),
                    new_visit_count=stats[best_inspector.id][
                        route.id
                    ],  # Лог перенесен сюда!
                )

            current_date += datetime.timedelta(days=1)

            day_log.info(
                "route_assigned_smart",
                route=route.title,
                inspector=best_inspector.get_full_name(),
                new_visit_count=stats[best_inspector.id][route.id],
            )

    # 4. ФИНАЛЬНОЕ СОХРАНЕНИЕ
    _save_generation_results(
        created_total,
        current_inspector_idx,
        inspectors,
        state,
        updated_stats_tracker,
        stats,
    )

    log.info(
        "generator_finished - Генератор закончил формировать расписание",
        total_created=created_total,
    )
    return f"Генерация завершена. Создано записей: {created_total}."
