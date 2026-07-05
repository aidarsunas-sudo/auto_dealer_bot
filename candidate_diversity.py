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

from collections import defaultdict, deque

MAX_PER_MODEL = 2


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
