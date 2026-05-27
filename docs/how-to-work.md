### ЧАСТЬ 1: Магия словарей и блокировки БД

#### 1. Как работает `stats = defaultdict(lambda: defaultdict(int))`?
Обычный словарь (`dict`) в Python при попытке обратиться к несуществующему ключу выдает ошибку `KeyError`.
`defaultdict` — это "умный" словарь. Если ты просишь у него ключ, которого нет, он не падает с ошибкой, а **создает его на лету** с дефолтным значением.

Когда мы пишем `defaultdict(int)`, дефолтное значение — это `0`.
Но у нас **двумерная матрица** (Человек -> Маршрут -> Количество).
Поэтому мы используем вложенность: `defaultdict(lambda: defaultdict(int))`.

**Пример из жизни:**
Ты спрашиваешь у словаря: *"Сколько раз Сидоров (id=5) был на Сборке (id=2)?"*
В коде это: `count = stats[5][2]`
Если Сидоров только вчера устроился, его нет в БД.
Обычный `dict` выдаст фатальную ошибку.
А наш `defaultdict` скажет: *"Ага, Сидорова нет. Создаю ему профиль. Маршрута 2 у него нет. Создаю. Возвращаю `0`"*.
Это позволяет нам делать `stats[best_inspector.id][route.id] += 1` в основном цикле **вообще не делая проверок `if exists`**. Код становится коротким и быстрым.

#### 2. Нужно ли делать блокировку `select_for_update()` для `db_stats`?
**Короткий ответ:** Нет, не нужно.
**Почему:** У нас в коде чуть ниже есть вот эта строчка:
`state, _ = ScheduleGeneratorState.objects.select_for_update().get_or_create(id=1)`
Она блокирует строку "закладки" в БД. Если два администратора одновременно нажмут кнопку "Сгенерировать", второй будет ждать, пока первый не закончит всю транзакцию. Эта закладка работает как **Глобальный Мьютекс (Global Mutex)** для всего процесса генерации. Состояние гонки (Race Condition) здесь исключено.

#### 3. Стоит ли вынести этот кусок в функцию?
**Определенно да.** Это самостоятельный кусок логики ("Загрузи статистику в память").
Вот как будет выглядеть хелпер:

```python
from collections import defaultdict
from typing import Tuple, Set

def _load_route_statistics() -> Tuple[defaultdict, Set]:
    """
    Загружает исторические счетчики маршрутов из БД в оперативную память.
    Возвращает:
    1. stats - двумерный словарь: stats[user_id][route_id] = количество_посещений
    2. updated_stats_tracker - пустое множество для отслеживания изменений
    """
    stats = defaultdict(lambda: defaultdict(int))
    db_stats = InspectorRouteStat.objects.all()

    for stat in db_stats:
        stats[stat.inspector_id][stat.route_id] = stat.visits_count

    updated_stats_tracker = set()
    return stats, updated_stats_tracker
```

---

### ЧАСТЬ 2: Разбор Основного Цикла (Main Loop)

Этот цикл — сердце алгоритма. Давай прочитаем его, как книгу.

```python
for _ in range(days_count):
```
Мы идем по дням (например, 21 день).

**ШАГ 0: Свободен ли день?**
```python
empty_routes = []
for route in routes:
    templates_in_route = route.templates.all()
    if templates_in_route and not Schedule.objects.filter(date=current_date, template=templates_in_route[0]).exists():
        empty_routes.append(route)
```
Мы проверяем каждый маршрут. Может быть, на 15 мая админ уже *вручную* назначил Петрова на маршрут "Сборка". Алгоритм видит это (`exists()`) и говорит: *"Ага, Сборка занята, я не буду никого туда ставить"*. Он собирает только полностью `empty_routes` (свободные маршруты) на сегодня.

**ШАГ А: Строгий Round-Robin (Набираем дежурных)**
```python
daily_inspectors = []
for _ in range(len(empty_routes)):
    daily_inspectors.append(inspectors[current_inspector_idx])
    current_inspector_idx = (current_inspector_idx + 1) % len(inspectors)
```
Допустим, у нас 5 свободных маршрутов. Мы берем из очереди ровно **5 человек** (по закладке). Мы еще не знаем, КУДА они пойдут. Мы просто вывели их "на плац". Это гарантирует, что общее количество рабочих смен у всех будет одинаковым.

**ШАГ Б: Умная тасовка (The Smart Shuffle)**
```python
for route in empty_routes:
    daily_inspectors.sort(key=lambda insp: (stats[insp.id][route.id], insp.id))
    best_inspector = daily_inspectors.pop(0)
```
Это самая гениальная часть твоего алгоритма.
У нас стоит 5 человек на плацу. Мы берем маршрут "ЭМО".
Команда `sort` выстраивает этих 5 человек в шеренгу по правилу: **"Кто из вас был на ЭМО меньше всех?"** (`stats[insp.id][route.id]`).
*(Если Иванов и Петров были там по 0 раз, вступает тайбрейкер `insp.id` — сортировка по табельному номеру, чтобы алгоритм не выдавал рандом каждый раз).*

Затем `pop(0)` берет самого первого из этой шеренги (тот, кто был на ЭМО реже всех) и **уводит его с плаца**.
Остается 4 человека. Мы берем следующий маршрут "Сборка" и повторяем сортировку для оставшихся 4-х. И так пока плац не опустеет.

---

### ЧАСТЬ 3: Рефакторинг (Делаем цикл чистым)

Основной цикл можно и нужно разгрузить. Давай вынесем поиск пустых маршрутов и финальное сохранение в хелперы. Смотри, как элегантно теперь выглядит весь код!

**Хелперы (можно положить выше в файле):**
```python
def _get_empty_routes_for_date(current_date, routes):
    """Возвращает список маршрутов, которые еще никем не заняты в этот день."""
    empty_routes = []
    for route in routes:
        templates = route.templates.all()
        if templates and not Schedule.objects.filter(date=current_date, template=templates[0]).exists():
            empty_routes.append(route)
    return empty_routes


def _save_generation_results(created_total, current_inspector_idx, inspectors, state, updated_stats_tracker, stats):
    """Батчевое сохранение результатов генерации в БД."""
    if created_total > 0:
        # Обновляем закладку
        last_assigned_idx = (current_inspector_idx - 1) % len(inspectors)
        state.last_user_id = inspectors[last_assigned_idx].id
        state.save()

        # Обновляем счетчики маршрутов
        for uid, rid in updated_stats_tracker:
            InspectorRouteStat.objects.update_or_create(
                inspector_id=uid,
                route_id=rid,
                defaults={'visits_count': stats[uid][rid]}
            )
```

**ОСНОВНАЯ ФУНКЦИЯ (Теперь она читается как поэзия):**
```python
def generate_schedule(start_date, days_count=21):
    log = logger.bind(service="schedule_generator", start_date=str(start_date))
    log.info("generator_started")

    routes = list(InspectionRoute.objects.all().order_by("order").prefetch_related("templates"))
    inspectors = list(User.objects.filter(is_active=True, can_perform_inspections=True).order_by("id"))

    if not routes or not inspectors:
        return "Ошибка: Не настроены Маршруты или нет инспекторов."

    # 1. ЗАГРУЖАЕМ СЧЕТЧИКИ (ХЕЛПЕР)
    stats, updated_stats_tracker = _load_route_statistics()

    with transaction.atomic():
        # 2. ОПРЕДЕЛЯЕМ ТОЧКУ СТАРТА (ХЕЛПЕР, КОТОРЫЙ МЫ СДЕЛАЛИ ДО ЭТОГО)
        state, _ = ScheduleGeneratorState.objects.select_for_update().get_or_create(id=1)
        current_inspector_idx = _determine_start_index(state.last_user_id, inspectors, log)

        created_total = 0
        current_date = start_date

        # 3. ОСНОВНОЙ ЦИКЛ ГЕНЕРАЦИИ
        for _ in range(days_count):
            if not is_working_day(current_date):
                current_date += datetime.timedelta(days=1)
                continue

            day_log = log.bind(date=str(current_date))

            # А. Ищем свободные маршруты на сегодня (ХЕЛПЕР)
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
                daily_inspectors.sort(key=lambda insp: (stats[insp.id][route.id], insp.id))
                best_inspector = daily_inspectors.pop(0)

                for tmpl in route.templates.all():
                    Schedule.objects.create(date=current_date, template=tmpl, inspector=best_inspector)
                    created_total += 1

                # Обновляем память
                stats[best_inspector.id][route.id] += 1
                updated_stats_tracker.add((best_inspector.id, route.id))

                day_log.info("route_assigned_smart", route=route.title, inspector=best_inspector.get_full_name())

            current_date += datetime.timedelta(days=1)

        # 4. ФИНАЛЬНОЕ СОХРАНЕНИЕ (ХЕЛПЕР)
        _save_generation_results(created_total, current_inspector_idx, inspectors, state, updated_stats_tracker, stats)

    log.info("generator_finished", total_created=created_total)
    return f"Генерация завершена. Создано записей: {created_total}."
```

Согласись, теперь функция `generate_schedule` помещается на один экран, её бизнес-логика кристально понятна, а вся рутина надежно спрятана в хелперах!
