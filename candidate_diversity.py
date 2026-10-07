# -*- coding: utf-8 -*-
"""
candidate_diversity.py — round-robin отбор кандидатов на публикацию для
main_pipeline.py, чтобы за один запуск не улетело подряд много объявлений
одной и той же модели (и тем более — все с одного источника).

Отдельно от published_log.select_candidates(): та функция отвечает за
дедуп/свежесть (что вообще МОЖНО публиковать), а diversify() — за порядок
и разнообразие того, что реально попадёт в выдачу за один прогон.

Работает в две вложенные "карусели":
1. Внутри одной модели (brand+model) — round-robin по источникам
   (kbchachacha -> encar -> charancha -> onbid -> снова kbchachacha...),
   чтобы не публиковать 5 объявлений одной модели с одного и того же сайта.
2. Между моделями — round-robin по моделям, с потолком MAX_PER_MODEL постов
   одной модели за запуск, чтобы не устраивать "спам" одной моделью, даже
   если у неё гораздо больше свежих объявлений, чем у остальных.
"""

from collections import Counter, defaultdict, deque

MAX_PER_MODEL = 2

# Полный список источников без логина — та же четвёрка, что в
# target_collector.NOLOGIN_TARGET_ADAPTERS/broad_collector.BROAD_ADAPTERS.
# Задан явно (не импортом оттуда), чтобы candidate_diversity.py не тянул
# Playwright-зависимые модули только ради имён источников — см. check_source_health().
ALL_SOURCES = ("kbchachacha", "encar", "charancha", "onbid")


def diversify(candidates: list, limit: int, max_per_model: int = MAX_PER_MODEL) -> list:
    """
    Args:
        candidates: отфильтрованный список машин (см.
                    published_log.select_candidates) — порядок входа не
                    важен, эта функция строит свой собственный порядок.
        limit: сколько машин вернуть максимум (POST_LIMIT).
        max_per_model: сколько максимум машин ОДНОЙ модели (brand+model)
                       вернуть за один вызов.

    Returns:
        Список машин длиной не более `limit`, с разнообразием по моделям
        и источникам внутри каждой модели.
    """
    # Группируем по (brand, model), внутри — по source, сохраняя исходный
    # относительный порядок (тот же porядок, что отдал select_candidates,
    # т.е. более старые first_seen — раньше).
    by_model_source = defaultdict(lambda: defaultdict(deque))
    model_order = []

    for car in candidates:
        model_key = (car.get("brand"), car.get("model"))
        if model_key not in by_model_source:
            model_order.append(model_key)
        by_model_source[model_key][car.get("source")].append(car)

    # Для каждой модели строим один плоский round-robin список по источникам.
    model_queues = {}
    for model_key in model_order:
        source_groups = by_model_source[model_key]
        sources = list(source_groups.keys())
        interleaved = deque()
        while any(source_groups[s] for s in sources):
            for s in sources:
                if source_groups[s]:
                    interleaved.append(source_groups[s].popleft())
        model_queues[model_key] = interleaved

    # Круговой обход самих моделей, с потолком max_per_model на модель.
    result = []
    per_model_count = defaultdict(int)
    active_models = deque(model_order)

    while active_models and len(result) < limit:
        model_key = active_models.popleft()
        queue = model_queues[model_key]

        if not queue or per_model_count[model_key] >= max_per_model:
            continue  # модель исчерпана или упёрлась в свой потолок — выбывает из карусели

        result.append(queue.popleft())
        per_model_count[model_key] += 1

        if queue and per_model_count[model_key] < max_per_model:
            active_models.append(model_key)  # ещё есть что взять — на следующий круг

    return result


def limit_consecutive_brand(candidates: list, recent_brand_tail: list, max_in_a_row: int = 2) -> list:
    """
    Переставляет candidates (уже прошедшие diversify()) так, чтобы одна и
    та же МАРКА (не модель — diversify()/MAX_PER_MODEL уже разводит модели,
    но разные модели одной марки — например, Hyundai Avante, потом Hyundai
    Casper — так и остаются "той же маркой подряд") не шла больше
    max_in_a_row раз подряд. Считает и хвост recent_brand_tail (последние
    ФАКТИЧЕСКИ опубликованные бренды, см. published_log.recent_posted_brand_sequence()) —
    чтобы не продолжать серию, начатую в предыдущем прогоне, а не только
    внутри текущего списка.

    Жадный алгоритм: держим "текущую серию" (марка + сколько подряд), на
    каждом шаге берём первого кандидата по порядку, чья марка не продлит
    серию сверх лимита; если такого нет (все оставшиеся — та же марка) —
    берём первого по порядку всё равно (повтор марки лучше, чем урезанный
    результат — тот же принцип, что у filter_recent_models()).
    """
    if not candidates:
        return candidates

    pending = list(candidates)
    result = []
    tail = list(recent_brand_tail[-max_in_a_row:]) if recent_brand_tail else []

    while pending:
        streak_brand = tail[-1] if tail else None
        streak_len = 0
        for b in reversed(tail):
            if b != streak_brand:
                break
            streak_len += 1

        if streak_brand is not None and streak_len >= max_in_a_row:
            pick_idx = next((i for i, c in enumerate(pending) if c.get("brand") != streak_brand), 0)
        else:
            pick_idx = 0

        chosen = pending.pop(pick_idx)
        result.append(chosen)
        tail.append(chosen.get("brand"))
        tail = tail[-max_in_a_row:]

    return result


def filter_recent_models(candidates: list, recent_models: set) -> list:
    """
    Убирает кандидатов моделей (brand, model), которые уже публиковались
    недавно (см. published_log.recently_posted_models) — ЕСЛИ после этого
    остаются другие кандидаты. Если после фильтра список пуст (то есть
    доступны ТОЛЬКО недавно опубликованные модели, альтернатив нет) —
    возвращает исходный список без изменений: лучше повтор модели, чем
    вообще не публиковать ничего за весь прогон.
    """
    if not recent_models:
        return candidates

    filtered = [c for c in candidates if (c.get("brand"), c.get("model")) not in recent_models]
    return filtered if filtered else candidates


def check_source_health(cars: list, log, notify_fn=None) -> list:
    """
    "Тихая смерть" источника (kbchachacha/encar/charancha/onbid) — площадка
    технически отвечает, но резолвер/поиск ничего не находит НИ ПО ОДНОЙ
    модели — иначе не видна: main_pipeline.py каждый прогон обходит все
    источники в try/except, сбой одного не мешает собрать данные с
    остальных, и лог просто тише в одном месте, а не падает целиком.
    Именно так kbchachacha молчал 13 дней, а charancha — 14, прежде чем
    это заметили (см. FINAL_STATE.md/аудит 2026-07-20).

    Считает cars (объединённый пул targeted+broad ЗА ЭТОТ прогон) по полю
    "source" — если источник дал 0 объявлений СУММАРНО (не только в одном
    из режимов), шлёт предупреждение через notify_fn (по умолчанию
    notifier.notify_error — параметризовано для тестов, чтобы не тянуть
    сетевой вызов).

    Returns:
        Список замолчавших источников (для лога вызывающего кода/тестов).
    """
    if notify_fn is None:
        from notifier import notify_error as notify_fn

    import source_health
    counts = Counter(car.get("source") for car in cars)
    # Отключённые флагом (Encar на GitHub) и срезанные предохранителем источники не «молчат»:
    # про них уже есть отдельное сообщение — второй раз тревогу не поднимаем.
    silent = [s for s in ALL_SOURCES if source_health.is_active(s) and counts.get(s, 0) == 0]

    if silent:
        message = (f"Источник(и) не дали НИ ОДНОГО объявления за этот прогон "
                    f"main_pipeline.py (targeted+broad суммарно): {', '.join(silent)}. "
                    f"Похоже на 'тихую смерть' резолвера/поиска — см. FINAL_STATE.md "
                    f"про инцидент 2026-07-20 (kbchachacha/charancha молчали неделями "
                    f"незамеченными).")
        log.warning(f"check_source_health: {message}")
        notify_fn(message)

    return silent
