from django.db import transaction

import structlog

from checklists.models import Schedule

logger = structlog.get_logger(__name__)


def cascade_shift_schedule(target_date, user_to_remove):
    """
    Каскадный сдвиг очереди.
    Сдвигает всех инспекторов вверх. Оставляет последний день периода пустым
    (он заполнится автоматически при следующем плановом запуске генератора).
    """
    log = logger.bind(
        service="cascade_shift",
        target_date=str(target_date),
        user_to_remove_from_scheduler=user_to_remove,
    )

    log.info("cascade_shift_started")

    with transaction.atomic():
        # 1. Получаем все будущие записи, отсортированные хронологически
        future_records = (
            Schedule.objects.select_for_update()
            .filter(date__gte=target_date, inspection__isnull=True, is_swapped=False)
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

            new_inspector_id = next_block["inspector_id"]

            # Берем ID задач ТЕКУЩЕГО блока и назначаем им инспектора из СЛЕДУЮЩЕГО блока
            updated_count = Schedule.objects.filter(
                id__in=target_block["schedule_ids"]
            ).update(inspector_id=new_inspector_id)

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

    log.info("cascade_shift_completed", shifted_blocks_count=shifted_blocks_count)
    return (
        True,
        "Очередь сдвинута. Освободившийся слот заполнится при следующей генерации.",
    )
