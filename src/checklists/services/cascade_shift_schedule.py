import datetime
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

import structlog

from checklists.models import Schedule
from checklists.tasks import notify_user_about_swap

logger = structlog.get_logger(__name__)


def cascade_shift_schedule(target_date, user_to_remove, is_silent=True):
    """
    Каскадный сдвиг очереди.
    Сдвигает всех инспекторов вверх. Оставляет последний день периода пустым
    (он заполнится автоматически при следующем плановом запуске генератора).
    """
    log = logger.bind(
        service="cascade_shift",
        target_date=str(target_date),
        user_to_remove_from_scheduler=user_to_remove,
        is_silent=is_silent,
    )

    log.info("cascade_shift_started")

    # Вычисляем границы текущей календарной недели (Пн - Вс)
    today = timezone.now().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())  # Понедельник
    end_of_week = start_of_week + datetime.timedelta(days=6)  # Воскресенье

    notifications_to_send = set()
    with transaction.atomic():
        # 1. Получаем все будущие записи, отсортированные хронологически
        # Берем либо конкретно ту смену, которую удаляем (даже если is_swapped=True)
        # Либо все будущие смены, которые не зафиксированы (is_swapped=False)
        condition = Q(date=target_date, inspector=user_to_remove) | Q(
            date__gte=target_date, is_swapped=False
        )

        future_records = (
            Schedule.objects.select_for_update()
            .filter(condition, inspection__isnull=True)
            .order_by("date", "id")
        )

        # 2. Группируем записи в "Блоки" (один инспектор в один день = один блок)
        blocks = []
        current_block_key = None
        current_schedule_ids = []

        for record in future_records:
            key = (record.date, record.inspector_id)
            if key != current_block_key:
                if current_block_key is not None:
                    blocks.append(
                        {
                            "date": current_block_key[0],
                            "inspector_id": current_block_key[1],
                            "schedule_ids": current_schedule_ids,
                        }
                    )
                current_block_key = key
                current_schedule_ids = [record.id]
            else:
                current_schedule_ids.append(record.id)

        # Не забываем последний блок
        if current_block_key is not None:
            blocks.append(
                {
                    "date": current_block_key[0],
                    "inspector_id": current_block_key[1],
                    "schedule_ids": current_schedule_ids,
                }
            )

        log.info("blocks_formed", total_blocks=len(blocks))

        # 3. Находим индекс блока, который нужно удалить
        remove_index = -1
        for i, block in enumerate(blocks):
            if (
                block["date"] == target_date
                and block["inspector_id"] == user_to_remove.id
            ):
                remove_index = i
                break

        if remove_index == -1:
            log.warning("cascade_shift_failed", reason="target_block_not_found")
            return (
                False,
                "У сотрудника нет неначатых задач на эту дату (или они зафиксированы).",
            )

        # Запоминаем количество задач, которые мы удаляем (для логов)
        tasks_count = len(blocks[remove_index]["schedule_ids"])
        log.info(
            "target_block_found", remove_index=remove_index, tasks_in_block=tasks_count
        )

        # 4. Сдвигаем инспекторов ВВЕРХ
        shifted_blocks_count = 0

        # Начинаем с удаляемого блока и идем до ПРЕДПОСЛЕДНЕГО
        for i in range(remove_index, len(blocks) - 1):
            target_block = blocks[i]
            next_block = blocks[i + 1]

            current_target_date = target_block["date"]
            new_inspector_id = next_block["inspector_id"]

            # Берем ID задач ТЕКУЩЕГО блока и назначаем им инспектора из СЛЕДУЮЩЕГО блока
            # Обновляем БД
            # ВАЖНО: принудительно ставим is_swapped=False.
            # Если мы удалили зафиксированную смену, для нового человека она должна стать обычной!
            updated_count = Schedule.objects.filter(
                id__in=target_block["schedule_ids"]
            ).update(
                inspector_id=new_inspector_id,
                is_swapped=False,  # <-- Снимаем флаг фиксации
            )

            # Проверка для уведомлений
            if not is_silent and (start_of_week <= current_target_date <= end_of_week):
                notifications_to_send.add((str(current_target_date), new_inspector_id))

            shifted_blocks_count += 1
            log.info(
                "block_shifted",
                date=str(target_block["date"]),
                old_inspector=target_block["inspector_id"],
                new_inspector=new_inspector_id,
                tasks_updated=updated_count,
            )

        # 5. Обрабатываем ПОСЛЕДНИЙ блок в цепочке
        # Так как все сдвинулись вверх, последний блок остался без инспектора.
        # Мы удаляем задачи этого блока, освобождая место.
        last_block = blocks[-1]
        deleted_tail_count, _ = Schedule.objects.filter(
            id__in=last_block["schedule_ids"]
        ).delete()

        log.info(
            "tail_cleared",
            date=str(last_block["date"]),
            deleted_tasks=deleted_tail_count,
        )

    # 6. ВЫПОЛНЯЕМ ОТПРАВКУ УВЕДОМЛЕНИЙ (ПОСЛЕ УСПЕШНОГО КОММИТА ТРАНЗАКЦИИ)
    # Вынесено за пределы with transaction.atomic()
    if notifications_to_send:
        log.info("triggering_cascade_notifications", count=len(notifications_to_send))
        for notify_date, notify_user_id in notifications_to_send:
            notify_user_about_swap.delay(notify_date, notify_user_id)

    log.info("cascade_shift_completed", shifted_blocks_count=shifted_blocks_count)
    return (
        True,
        "Очередь сдвинута. Освободившийся слот заполнится при следующей генерации.",
    )
