from checklists.models import InspectionItem
from checklists.services.services import get_item_history_chain


# Вспомогательная функция для сборки "Вчерашней" истории (только для повторов)
def _fetch_history(item, depth):
    # Немного модифицируем вызов, чтобы он возвращал нужную глубину
    # Если depth=2, нам нужна 1 прошлая запись. Если >=3, берем 2 прошлые.
    return get_item_history_chain(item, max_depth=(depth - 1))


def _format_history(history_chain):
    history_data = []
    for past in history_chain:
        history_data.append(
            {
                "date": past.inspection.date_check.strftime("%d.%m.%Y"),
                "inspector": past.inspection.inspector.get_full_name(),
                "comment": past.comment,
                "photos": [p.image.url for p in past.photos.all()],
            }
        )
    return history_data


def get_violations_report_data(start_date, end_date, location_id=None):
    # 1. Базовый запрос
    query = InspectionItem.objects.filter(
        inspection__is_completed=True,
        inspection__date_check__range=[start_date, end_date],
        is_compliant=False,
    )

    # --- 2. НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ---
    if location_id == "b1_only":
        # Выбираем ТОЛЬКО раздел B1 по всему заводу
        query = query.filter(section_type="B1")
    elif location_id and str(location_id) != "all":
        # Фильтруем по конкретному участку
        query = query.filter(inspection__template__location_id=location_id)

    # Оптимизация
    bad_items = (
        query.select_related(
            "inspection",
            "inspection__inspector",
            "inspection__template__location",
            "criteria_origin",
        )
        .prefetch_related("photos")
        .order_by("-inspection__date_check")
    )

    report = {
        "single": {"count": 0, "items": []},
        "repeated": {"count": 0, "items": []},
        "chronic": {"count": 0, "items": []},
    }

    for item in bad_items:
        item_data = {
            "id": item.id,
            "location_name": item.inspection.template.location.name,
            "date": item.inspection.date_check.strftime("%d.%m.%Y"),
            # Используем get_short_name (Иванов И. И.) или get_full_name
            "inspector": item.inspection.inspector.get_full_name(),
            "section": item.section_name,
            "criteria": item.criteria_text,
            "comment": item.comment,
            "photos": [photo.image.url for photo in item.photos.all()],
            "history": [],
        }

        if item.consecutive_violations <= 1:
            report["single"]["items"].append(item_data)
            report["single"]["count"] += 1
        elif item.consecutive_violations == 2:
            item_data["history"] = _format_history(_fetch_history(item, 2))
            report["repeated"]["items"].append(item_data)
            report["repeated"]["count"] += 1
        else:
            item_data["history"] = _format_history(_fetch_history(item, 3))
            report["chronic"]["items"].append(item_data)
            report["chronic"]["count"] += 1

    return report
