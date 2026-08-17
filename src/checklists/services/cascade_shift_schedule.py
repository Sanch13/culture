import datetime
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

import structlog

from checklists.models import Schedule, SwapLog
from checklists.tasks import notify_user_about_swap

logger = structlog.get_logger(__name__)


def cascade_shift_schedule(target_schedule, requestor, is_silent=True):
    """
    Каскадный сдвиг очереди по КОНКРЕТНОМУ маршруту.
    """
    target_date = target_schedule.date
    user_to_remove = target_schedule.inspector

    # Определяем, к какому маршруту относится кликнутая задача
    # Берем первый маршрут из M2M связи шаблона
    target_route = target_schedule.template.routes.first()
    target_route_id = target_route.id if target_route else None

    log = logger.bind(
        service="cascade_shift",
        target_date=str(target_date),
        user_to_remove=user_to_remove.id,
        target_route_id=target_route_id,
        is_silent=is_silent,
    )
    log.info("cascade_shift_started")

    today = timezone.now().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)

    notifications_to_send = set()
    with transaction.atomic():
        condition = Q(date=target_date, inspector=user_to_remove) | Q(
            date__gte=target_date, is_swapped=False
        )

        # ИЗМЕНЕНИЕ: Оптимизируем запрос, подтягивая связи маршрутов
        future_records = (
            Schedule.objects.select_for_update()
            .filter(condition, inspection__isnull=True)
            .select_related("template")
            .prefetch_related("template__routes")
            .order_by("date", "id")
        )

        blocks = []
        current_block_key = None
        current_schedule_ids = []

        for record in future_records:
            # Вычисляем ID маршрута для текущей записи
            record_route = record.template.routes.first()
            record_route_id = record_route.id if record_route else None

            # ИЗМЕНЕНИЕ: Ключ группировки теперь включает ID МАРШРУТА!
            key = (record.date, record.inspector_id, record_route_id)

            if key != current_block_key:
                if current_block_key is not None:
                    blocks.append(
                        {
                            "date": current_block_key[0],
                            "inspector_id": current_block_key[1],
                            "route_id": current_block_key[2],  # Сохраняем маршрут
                            "schedule_ids": current_schedule_ids,
                        }
                    )
                current_block_key = key
                current_schedule_ids = [record.id]
            else:
                current_schedule_ids.append(record.id)

        if current_block_key is not None:
            blocks.append(
                {
                    "date": current_block_key[0],
                    "inspector_id": current_block_key[1],
                    "route_id": current_block_key[2],
                    "schedule_ids": current_schedule_ids,
                }
            )

        # 3. Находим индекс блока, который нужно удалить
        remove_index = -1
        for i, block in enumerate(blocks):
            # ИЗМЕНЕНИЕ: Ищем строго совпадение Даты + Юзера + МАРШРУТА
            if (
                block["date"] == target_date
                and block["inspector_id"] == user_to_remove.id
                and block["route_id"] == target_route_id
            ):
                remove_index = i
                break

        if remove_index == -1:
            log.warning("cascade_shift_failed", reason="target_block_not_found")
            return (
                False,
                "У сотрудника нет неначатых задач на эту дату по этому маршруту.",
            )

        # Дальше логика сдвига остается без изменений!
        tasks_count = len(blocks[remove_index]["schedule_ids"])
        log.info(
            "target_block_found", remove_index=remove_index, tasks_in_block=tasks_count
        )

        shifted_blocks_count = 0

        for i in range(remove_index, len(blocks) - 1):
            target_block = blocks[i]
            next_block = blocks[i + 1]

            current_target_date = target_block["date"]
            new_inspector_id = next_block["inspector_id"]

            updated_count = Schedule.objects.filter(
                id__in=target_block["schedule_ids"]
            ).update(
                inspector_id=new_inspector_id,
                is_swapped=False,
            )

            log.info(
                "block_shifted",
                date=str(current_target_date),
                new_inspector_id=new_inspector_id,
                tasks_updated=updated_count,
            )

            if not is_silent and (start_of_week <= current_target_date <= end_of_week):
                notifications_to_send.add((str(current_target_date), new_inspector_id))

            shifted_blocks_count += 1

        last_block = blocks[-1]
        Schedule.objects.filter(id__in=last_block["schedule_ids"]).delete()

    # Запись лога и рассылка...
    route_name = target_route.title if target_route else "Неизвестный маршрут"
    reason_text = f"Удаление из графика: {user_to_remove.last_name} {user_to_remove.first_name} на {target_date} (Маршрут: {route_name})"
    if is_silent:
        reason_text += " [Тихий сдвиг]"

    SwapLog.objects.create(
        requestor=requestor,
        target_user=user_to_remove,
        source_date=target_date,
        target_date=target_date,
        reason=reason_text,
    )

    if notifications_to_send:
        for notify_date, notify_user_id in notifications_to_send:
            notify_user_about_swap.delay(notify_date, notify_user_id)

    return (
        True,
        "Очередь сдвинута. Освободившийся слот заполнится при следующей генерации.",
    )
