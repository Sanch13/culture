import datetime
from itertools import groupby

import holidays

from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Q, Avg, Min
from django.utils import timezone
from django.core.paginator import Paginator

from checklists.models import (
    Inspection,
    InspectionItem,
    Schedule,
    InspectionRoute,
    SwapLog,
    Location,
    InspectionSectionScore,
    LocationDailyScore,
)
from checklists.utils import format_phone_number

User = get_user_model()


def generate_schedule(start_date, days_count=7):
    """
    Генерирует расписание на основе МАРШРУТОВ (InspectionRoute).
    """
    by_holidays = holidays.BY()

    # 1. Загружаем МАРШРУТЫ (а не шаблоны)
    # Сортируем по order, чтобы порядок раздачи был фиксирован
    # prefetch_related('templates') загружает связанные шаблоны сразу, чтобы не тормозить
    routes = list(
        InspectionRoute.objects.all().order_by("order").prefetch_related("templates")
    )

    inspectors = list(
        User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id")
    )

    if not routes:
        return (
            "Ошибка: Не настроены Маршруты (InspectionRoute). Заполните их в админке."
        )
    if not inspectors:
        return "Ошибка: Нет сотрудников-инспекторов."

    # 2. ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА ОЧЕРЕДИ
    last_entry = Schedule.objects.order_by("-date", "-id").first()

    start_index = 0
    if last_entry:
        try:
            last_inspector_index = inspectors.index(last_entry.inspector)
            start_index = (last_inspector_index + 1) % len(inspectors)
        except ValueError:
            start_index = 0

    current_inspector_idx = start_index
    created_total = 0
    current_date = start_date

    with transaction.atomic():
        for _ in range(days_count):
            # Пропуск выходных/праздников
            if current_date.weekday() >= 5:
                current_date += datetime.timedelta(days=1)
                continue

            if current_date in by_holidays:
                current_date += datetime.timedelta(days=1)
                continue

            # 3. НАЗНАЧЕНИЕ ПО МАРШРУТАМ
            for route in routes:
                inspector = inspectors[current_inspector_idx]

                # Получаем список шаблонов внутри этого маршрута (например, Раздув + ЦПМ)
                templates_in_route = route.templates.all()

                if not templates_in_route:
                    # Пустой маршрут? Пропускаем, очередь не двигаем
                    continue

                # Флаг: удалось ли что-то назначить?
                assigned_something = False

                # Назначаем ЭТОМУ ЖЕ инспектору ВСЕ шаблоны из маршрута
                for tmpl in templates_in_route:
                    # Защита от дублей: если на этот день и шаблон уже есть запись -> пропускаем
                    if not Schedule.objects.filter(
                        date=current_date, template=tmpl
                    ).exists():
                        Schedule.objects.create(
                            date=current_date, template=tmpl, inspector=inspector
                        )
                        created_total += 1
                        assigned_something = True

                # Сдвигаем очередь ТОЛЬКО если мы реально назначили работу
                if assigned_something:
                    current_inspector_idx = (current_inspector_idx + 1) % len(
                        inspectors
                    )

            current_date += datetime.timedelta(days=1)

    return f"Генерация завершена. Создано записей: {created_total}."


def create_inspection_from_template(template, user, date, location_snapshot):
    """
    Бизнес-логика: Создание экземпляра проверки на основе шаблона.
    Здесь происходит копирование вопросов (Snapshot).
    """

    # transaction.atomic() гарантирует: либо создастся всё целиком,
    # либо (если произойдет ошибка) не создастся ничего. Не будет "половинчатых" отчетов.
    with transaction.atomic():
        # 1. Создаем шапку отчета
        inspection = Inspection.objects.create(
            template=template,
            inspector=user,
            date_check=date,
            location_snapshot=location_snapshot,
        )

        # 2. Получаем все разделы шаблона (упорядоченные по order)
        sections = template.sections.all().order_by("order")

        # 3. Итерируемся по разделам
        for section in sections:
            # Получаем вопросы внутри раздела
            criteria_list = section.criteria.all().order_by("order")

            for criteria in criteria_list:
                # 4. Создаем строку отчета (Snapshot)
                InspectionItem.objects.create(
                    inspection=inspection,
                    criteria_origin=criteria,
                    section_name=section.title,
                    section_type=section.section_type,
                    criteria_text=criteria.text,
                    criteria_order=criteria.order,
                    is_compliant=True,
                )

        return inspection


def perform_auto_swap(schedule_item, reason):
    """
    Групповая автозамена.
    Меняет ВСЕ смены инициатора на эту дату на ВСЕ смены жертвы на будущую дату.
    """
    current_date = schedule_item.date
    current_user = schedule_item.inspector

    # 1. Находим ВСЕ задачи инициатора на этот день (чтобы отдать их все)
    current_tasks = list(
        Schedule.objects.filter(date=current_date, inspector=current_user)
    )

    # Защита: вдруг хоть один из отчетов уже начат? (Хотя view это уже проверила)
    for t in current_tasks:
        if t.inspection:
            return (
                False,
                "Нельзя обменять смену, так как часть проверок уже начата.",
                "",
            )

    # 2. Вычисляем дату начала следующей недели
    today = timezone.now().date()
    days_until_next_monday = 7 - today.weekday()
    if days_until_next_monday <= 0:
        days_until_next_monday = 7
    start_of_next_week = today + datetime.timedelta(days=days_until_next_monday)

    # 3. Ищем кандидата-жертву (любую неначатую смену на следующей неделе)
    candidate_task = (
        Schedule.objects.filter(
            date__gte=start_of_next_week,
            inspection__isnull=True,
            is_swapped=False,  # Только тех, кто еще не менялся
        )
        .exclude(inspector=current_user)
        .order_by("date", "id")
        .first()
    )

    if not candidate_task:
        return (
            False,
            "Нет доступных кандидатов на следующей неделе. Сообщите администратору.",
            "",
        )

    target_date = candidate_task.date
    target_user = candidate_task.inspector

    # 4. Находим ВСЕ задачи жертвы на тот день (чтобы забрать их все)
    target_tasks = list(
        Schedule.objects.filter(date=target_date, inspector=target_user)
    )

    # 5. СОВЕРШАЕМ ОБМЕН
    with transaction.atomic():
        # Мои задачи отдаем ему
        for t in current_tasks:
            t.inspector = target_user
            t.is_swapped = False  # Оставляем False, чтобы жертва могла отказаться
            t.save()

        # Его задачи забираю я
        for t in target_tasks:
            t.inspector = current_user
            t.is_swapped = True  # Я переехал, меня больше трогать нельзя
            t.save()

        # 6. Пишем лог (ОДИН лог на весь обмен, не нужно плодить дубли)
        SwapLog.objects.create(
            requestor=current_user,
            target_user=target_user,
            source_date=current_date,
            target_date=target_date,
            reason=reason,
        )

    info_about_change = (
        f"Дата: {current_date}.\nПроверяющий {current_user.last_name} {current_user.first_name} "
        f"заменился на {target_user.last_name} {target_user.first_name}.\nПричина: {reason}"
    )

    return (
        True,
        f"Обмен выполнен. Вы перенесены на {target_date.strftime('%d.%m')}. Вместо вас выйдет {target_user.last_name}.",
        info_about_change,
    )


def _get_production_chief_text():
    """
    Возвращает готовый текст с контактами Начальника производства.
    Использует кэширование запроса (если нужно), но пока просто берет из БД.
    """
    chief = User.objects.filter(role=User.ROLE_PRODUCTION_CHIEF, is_active=True).first()

    if not chief:
        return ""

    phone = format_phone_number(chief.phone) or "нет телефона"
    return (
        f"\n------------------------\n"
        f"❗️ В случае отсутствия кого-то из руководителей производственного участка "
        f"обращаться к Начальнику производства:\n"
        f"{chief.first_name} {chief.last_name} (Тел: {phone})"
    )


def _get_location_contacts_text(location):
    """
    Собирает контакты участка (Начальник, Замы, Ст. мастера, Мастера).
    """
    lines = []

    # 1. Начальник участка (Один)
    if location.manager:
        p = format_phone_number(location.manager.phone)
        lines.append(f"👤 Начальник участка: {location.manager.get_full_name()} ({p})")

    # 2. Заместители (Много)
    # Используем .all() так как теперь это ManyToMany
    deputies = location.deputies.all()
    if deputies:
        # Если зам один - пишем красиво в одну строку (опционально), но проще списком
        lines.append("👤 Заместители начальника:")
        for dep in deputies:
            p = format_phone_number(dep.phone)
            lines.append(f"   - {dep.get_full_name()} ({p})")

    # 3. Старшие мастера (Много)
    senior_masters = location.senior_masters.all()
    if senior_masters:
        lines.append("👷‍♂️ Старшие мастера:")
        for sm in senior_masters:
            p = format_phone_number(sm.phone)
            lines.append(f"   - {sm.get_full_name()} ({p})")

    # 4. Мастера (Много)
    masters = location.masters.all()
    if masters:
        lines.append("👷 Мастера:")
        for m in masters:
            p = format_phone_number(m.phone)
            lines.append(f"   - {m.get_full_name()} ({p})")

    if not lines:
        return "Локальные контакты не назначены."

    return "\n".join(lines)


def build_composite_email_body(schedules, intro_message):
    """
    Генерирует полный текст письма для нескольких шаблонов и участков.
    """
    inspector = schedules[0].inspector
    date_str = schedules[0].date.strftime("%d.%m.%Y")

    # 1. Шапка
    body = [
        f"Здравствуйте, {inspector.first_name}!\n",
        f"{intro_message}\n",
        f"📅 Дата смены: {date_str}\n",
    ]

    # 2. Собираем уникальные участки и список шаблонов
    unique_locations = set()
    template_names = []

    for item in schedules:
        location = item.template.location
        unique_locations.add(location)
        template_names.append(f" - {item.template.name} (Участок: {location.name})")

    body.append("📋 Вам назначены следующие проверки:")
    body.extend(template_names)
    body.append("\n--- КОНТАКТЫ ДЛЯ СВЯЗИ ---")

    # 3. Выводим контакты только для уникальных участков
    for location in unique_locations:
        body.append(f"\n🏢 {location.name.upper()}")
        body.append(_get_location_contacts_text(location))

    # 4. Добавляем главного босса (один раз в конце)
    body.append(_get_production_chief_text())

    body.append("\n------------------------")
    body.append("Пожалуйста, не забудьте заполнить отчеты в системе.")

    return "\n".join(body)


def get_swap_notification_data(date_str, user_id):
    """
    Собирает данные для письма на основе ВСЕХ задач инспектора на конкретный день.
    """
    # 1. Загружаем все задачи (расписание) человека на этот день
    schedules = list(
        Schedule.objects.filter(date=date_str, inspector_id=user_id)
        .select_related(
            "inspector",
            "template",
            "template__location",
            "template__location__manager",
        )
        .prefetch_related(
            "template__location__masters",
            "template__location__deputies",
            "template__location__senior_masters",
        )
    )

    if not schedules:
        return None

    inspector = schedules[0].inspector
    if not inspector.email:
        return None

    # Уникальное сообщение для ЗАМЕНЫ
    intro = "⚠️ ВНИМАНИЕ: Вам назначена новая смена (в порядке замены)."

    # 2. Вызываем строитель, передавая СПИСОК расписаний
    body = build_composite_email_body(schedules, intro)

    return {
        "email": inspector.email,
        "subject": f"⚡ Назначение смены: {date_str}",
        "body": body,
    }


def prepare_daily_notifications(target_date=None):
    """
    Данные для ЕЖЕДНЕВНОЙ рассылки.
    """
    if target_date is None:
        target_date = datetime.date.today()

    # 1. ЗАГРУЖАЕМ РАСПИСАНИЕ
    # ОБЯЗАТЕЛЬНО добавляем order_by('inspector_id') для правильной работы groupby!
    schedules = (
        Schedule.objects.filter(date=target_date)
        .select_related(
            "inspector",
            "template",
            "template__location",
            "template__location__manager",
        )
        .prefetch_related(
            "template__location__masters",
            "template__location__deputies",
            "template__location__senior_masters",
        )
        .order_by("inspector_id")
    )

    notifications_data = []

    # 2. ГРУППИРУЕМ ЗАДАЧИ ПО ИНСПЕКТОРУ
    # groupby собирает все задачи одного инспектора вместе
    for inspector, items_iter in groupby(schedules, key=lambda s: s.inspector):
        if not inspector.email:
            continue

        # Превращаем итератор в полноценный список
        # Например: user_tasks = [Задача 1 (Раздув), Задача 2 (ЦПМ)]
        user_tasks = list(items_iter)

        intro = "Напоминаем, что у вас запланирована плановая проверка."

        body = build_composite_email_body(user_tasks, intro)

        # Берем первый шаблон из списка задач сотрудника
        first_template = user_tasks[0].template

        # Ищем маршрут, к которому привязан этот шаблон
        # (Используем .first(), так как мы предполагаем, что шаблон входит в 1 маршрут)
        route = first_template.routes.first()

        if route:
            # Если маршрут найден - пишем его имя
            route_name = route.title
        else:
            # Если маршрута почему-то нет (например, шаблон назначен вручную без маршрута)
            # - пишем просто "Несколько участков" или название первого участка
            route_name = first_template.location.name

        subject = f"Напоминание о проверке: {route_name}"

        notifications_data.append(
            {
                "recipient_email": inspector.email,
                "subject": subject,
                "body": body,
            }
        )

    # Подмена для теста ПОТОМ удалить !!!
    for item in notifications_data:
        item["recipient_email"] = "a.zubchyk@miran-bel.com"
    # Подмена для теста ПОТОМ удалить !!!

    return notifications_data


def apply_inspection_filters_and_paginate(request, base_queryset):
    """
    Применяет фильтры по дате и нарушениям к QuerySet отчетов (Inspection).
    Делает пагинацию по 20 элементов.
    Возвращает словарь с page_obj и текущими фильтрами для шаблона.
    """
    queryset = base_queryset

    # 1. Читаем параметры из URL (GET), заменяем None на пустую строку ''
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    only_violations = request.GET.get("only_violations", "")

    # 2. Применяем фильтры
    if date_from:
        queryset = queryset.filter(date_check__gte=date_from)

    if date_to:
        queryset = queryset.filter(date_check__lte=date_to)

    if only_violations == "on":
        # Предполагается, что queryset уже аннотирован .annotate(violation_count=...)
        queryset = queryset.filter(violation_count__gt=0)

    # 3. Пагинация
    paginator = Paginator(queryset, 20)  # 20 штук на страницу
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # 4. Возвращаем готовый кусок контекста
    return {
        "page_obj": page_obj,
        "filter_date_from": date_from,
        "filter_date_to": date_to,
        "filter_only_violations": only_violations,
    }


def is_global_viewer(user):
    """
    Определяет, имеет ли пользователь право видеть ВСЕ отчеты на заводе.
    """
    # 1. Глобальные роли по умолчанию
    if user.role in [
        User.ROLE_PRODUCTION_CHIEF,
        User.ROLE_ENGINEER_CHIEF,
        User.ROLE_ADMIN,
    ]:
        return True

    # 2. Суперпользователи Django
    if user.is_superuser:
        return True

    # 3. Привилегия участка ЭМО
    # Проверяем, является ли пользователь начальником/мастером на участке,
    # название которого содержит "ЭМО".
    # (Используем name__icontains="ЭМО" для гибкости, вдруг участок назовут "Участок ЭМО")

    is_emo_boss = Location.objects.filter(
        Q(name__icontains="ЭМО") | Q(name__icontains="Энерго-механический отдел"),
        Q(manager=user) | Q(deputies=user) | Q(senior_masters=user) | Q(masters=user),
    ).exists()

    if is_emo_boss:
        return True

    # Иначе - это обычный локальный босс (или рабочий)
    return False


def calculate_inspection_score(inspection):
    """
    ЭТАП 1: Высчитываем баллы для каждого РАЗДЕЛА (A, B1, B2, C...)
    ЭТАП 2: Высчитываем итоговый балл ОТЧЕТА.
    """
    items = inspection.items.all()
    if not items:
        return

    # --- 1. ОЦЕНКА РАЗДЕЛОВ ---

    # Группируем вопросы по типам разделов (A, B1, C...)
    sections_dict = {}
    for item in items:
        if not item.is_compliant:
            if item.is_repeated_violation:
                yesterday_item = (
                    InspectionItem.objects.filter(
                        inspection__template=inspection.template,
                        inspection__is_completed=True,
                        inspection__date_check__lt=inspection.date_check,
                        criteria_origin_id=item.criteria_origin_id,
                    )
                    .order_by("-inspection__date_check")
                    .first()
                )

                item.consecutive_violations = (
                    (yesterday_item.consecutive_violations + 1) if yesterday_item else 2
                )
            else:
                item.consecutive_violations = 1
        else:
            item.consecutive_violations = 0

        item.save(update_fields=["consecutive_violations"])

        stype = getattr(item, "section_type", "GENERAL")
        if not stype:
            stype = "GENERAL"

        if stype not in sections_dict:
            sections_dict[stype] = {"name": item.section_name, "items": []}

        sections_dict[stype]["items"].append(item)

    InspectionSectionScore.objects.filter(inspection=inspection).delete()

    # Словарь для хранения посчитанных баллов: {'A': 5.0, 'B1': 3.0, 'C': 6.0}
    calculated_section_scores = {}

    for stype, data in sections_dict.items():
        # Ищем самую большую серию повторов внутри этого раздела
        max_repeats = max([i.consecutive_violations for i in data["items"]] + [0])

        has_repeat_penalty = False

        # ЛОГИКА ОЦЕНКИ РАЗДЕЛА (по твоему ТЗ)
        if max_repeats >= 3:
            score = 2.0  # Двойной повтор (и более)
            has_repeat_penalty = True
        elif max_repeats == 2:
            score = 3.0  # Первый повтор
            has_repeat_penalty = True
        else:
            # Повторов нет. Считаем обычные нарушения (долю)
            total_q = len(data["items"])
            bad_q = sum(1 for i in data["items"] if not i.is_compliant)
            score = (
                round(6.0 * ((total_q - bad_q) / total_q), 2) if total_q > 0 else 6.0
            )

        # Сохраняем балл Раздела в БД
        InspectionSectionScore.objects.create(
            inspection=inspection,
            date_check=inspection.date_check,
            section_name=data["name"],
            section_type=stype,
            score=score,
        )
        calculated_section_scores[stype] = {
            "score": score,
            "has_repeat_penalty": has_repeat_penalty,
        }

    # --- 2. ОЦЕНКА ОТЧЕТА В ЦЕЛОМ ---

    # Исключаем B1 и D (они не влияют на средний балл текущего отчета)
    report_scores_data = [
        data
        for stype, data in calculated_section_scores.items()
        if stype
        not in [
            "B1",
        ]
    ]

    if not report_scores_data:
        final_report_score = 6.0  # Если отчет состоял только из B1 или D
    else:
        # Проверяем жесткое правило: Если хоть один раздел (A, B2, C) получил 3 или 2,
        # то весь отчет получает этот низший балл!
        # Ищем, был ли хоть в одном учитываемом разделе (A, B2, C, D) штраф за повтор
        penalties = [d["score"] for d in report_scores_data if d["has_repeat_penalty"]]

        if penalties:
            # Если есть штрафы (3.0 или 2.0), берем самый жесткий (минимальный)
            # ТЗ: "Если было повторяющиеся нарушение... просто ставится общая оценка 3/2"
            final_report_score = min(penalties)
        else:
            # Если штрафов за повтор нет -> просто считаем честное среднее
            # (даже если там есть 1.0 за кучу новых нарушений)
            all_scores = [d["score"] for d in report_scores_data]
            final_report_score = sum(all_scores) / len(all_scores)

    # Сохраняем балл Отчета в БД
    inspection.final_score = round(final_report_score, 2)
    inspection.save(update_fields=["final_score"])

    return inspection.final_score


def calculate_daily_location_scores(target_date):
    """
    ЭТАП 3: Высчитываем итоговый балл каждого УЧАСТКА за день.
    """
    locations = Location.objects.all()

    # 1. ГЛОБАЛЬНЫЙ B1 (Для ЭМО)
    # Берем ВСЕ сохраненные баллы разделов B1 за сегодня по всему заводу
    all_b1_scores = InspectionSectionScore.objects.filter(
        date_check=target_date, section_type="B1"
    )

    global_b1_score = None
    if all_b1_scores.exists():
        # Если где-то есть повтор (штраф 3 или 2) - он роняет весь B1 завода
        min_b1 = all_b1_scores.aggregate(Min("score"))["score__min"]
        if min_b1 <= 3.01:
            global_b1_score = min_b1
        else:
            global_b1_score = all_b1_scores.aggregate(Avg("score"))["score__avg"]

    # 2. РАСЧЕТ БАЛЛОВ УЧАСТКОВ
    for loc in locations:
        final_loc_score = None

        # --- ЛОГИКА ЭМО ---
        if "ЭМО" in loc.name:
            # Берем балл раздела D (из отчетов этого участка)
            score_d = InspectionSectionScore.objects.filter(
                inspection__template__location=loc,
                date_check=target_date,
                section_type="D",
            ).aggregate(Avg("score"))["score__avg"]

            # Считаем среднее между D и глобальным B1
            if score_d is not None and global_b1_score is not None:
                final_loc_score = (score_d + global_b1_score) / 2
            elif score_d is not None:
                final_loc_score = score_d
            elif global_b1_score is not None:
                final_loc_score = global_b1_score

        # --- ЛОГИКА ОСТАЛЬНЫХ УЧАСТКОВ (УПП, Сборка, Инструментальный...) ---
        else:
            # Берем ГОТОВЫЕ баллы (final_score) всех сданных отчетов этого участка
            inspections = Inspection.objects.filter(
                template__location=loc,
                date_check=target_date,
                is_completed=True,
                final_score__isnull=False,
            )

            if inspections.exists():
                # Просто считаем среднее арифметическое отчетов (например, 4-х отчетов УПП)
                scores = [insp.final_score for insp in inspections]
                final_loc_score = sum(scores) / len(scores)

        # 3. СОХРАНЯЕМ БАЛЛ УЧАСТКА В БД
        LocationDailyScore.objects.update_or_create(
            location=loc,
            date=target_date,
            defaults={
                "score": round(final_loc_score, 2)
                if final_loc_score is not None
                else None
            },
        )
