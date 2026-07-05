# -*- coding: utf-8 -*-
"""
target_model_resolver.py — общая инфраструктура для "целевых моделей"
(target_models.json), которую использует и orchestrator.py (4 источника без
логина), и model_search.py (все 9 источников, ручной запуск).

Что решает:
- target_models.json пользователь редактирует руками (никаких изменений
  кода не требуется, чтобы добавить новую модель).
- Каждый сайт-источник требует СВОИХ внутренних кодов марки/модели (числа,
  ULID, иногда просто текст) — их поиск иногда "дорогой" (через Playwright,
  клики по каскадным фильтрам). Резолвинг делается один раз на новую пару
  (источник, модель) и кэшируется в target_models_cache.json — при
  повторных запусках дальше идёт только сам запрос списка объявлений.

Используется так:
    from target_model_resolver import load_target_models, resolve

    targets = load_target_models()
    for target in targets:
        codes = resolve("encar", target["brand"], target["model"], my_lookup_fn, log)
        if codes is None:
            continue  # источник не смог найти модель — пропускаем, не падаем
        ...
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

TARGET_MODELS_PATH = Path(__file__).resolve().parent / "target_models.json"
CACHE_PATH = Path(__file__).resolve().parent / "target_models_cache.json"

# Сколько хранить отрицательный результат ("код не найден"), прежде чем
# попробовать снова — вдруг сайт был временно недоступен или модель
# появилась в каталоге позже. Успешные резолвинги хранятся бессрочно —
# коды марки/модели на сайтах меняются исключительно редко.
NEGATIVE_CACHE_TTL_DAYS = 7

logger = logging.getLogger("target_model_resolver")


def load_target_models(path: Path = TARGET_MODELS_PATH) -> list:
    """
    Загружает список целевых моделей из JSON. Отсутствие файла — НЕ ошибка
    (просто "дополнительных целевых моделей не задано"), пайплайн продолжает
    работать на постоянных WATCHLIST/USED_CAR_SEARCHES.
    """
    if not path.exists():
        logger.info(f"{path.name} не найден — дополнительных целевых моделей нет")
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось разобрать {path.name}: {error}")
        return []

    if not isinstance(data, list):
        logger.error(f"{path.name} должен быть списком объектов, получено: {type(data)}")
        return []

    return data


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Не удалось сохранить {CACHE_PATH.name}: {error}")


def _cache_key(source_name: str, brand: str, model: str) -> str:
    return f"{source_name}:{brand}:{model}"


def resolve(
    source_name: str,
    brand: str,
    model: str,
    lookup_fn: Callable[[str, str], Optional[dict]],
    log: logging.Logger,
) -> Optional[dict]:
    """
    Возвращает site-specific коды марки/модели, используя кэш, где возможно.

    Args:
        source_name: короткое имя источника ("encar", "kbchachacha", ...) —
                     часть ключа кэша.
        brand, model: как в target_models.json (человекочитаемый текст).
        lookup_fn: функция (brand, model) -> dict | None, которая реально
                   ищет коды на сайте (может использовать Playwright) —
                   вызывается ТОЛЬКО при промахе кэша.
        log: логгер вызывающего кода.

    Returns:
        dict с site-specific кодами (формат — на усмотрение конкретного
        источника) или None, если источник не смог найти соответствие.
    """
    cache = _load_cache()
    key = _cache_key(source_name, brand, model)
    entry = cache.get(key)

    if entry is not None:
        resolved_at = datetime.fromisoformat(entry["resolved_at"])
        is_negative = entry["result"] is None
        expired = is_negative and (datetime.now() - resolved_at) > timedelta(days=NEGATIVE_CACHE_TTL_DAYS)
        if not expired:
            return entry["result"]
        log.info(f"{source_name}: кэш для {brand} {model} истёк (отрицательный результат старше "
                  f"{NEGATIVE_CACHE_TTL_DAYS} дн.), пробую снова")

    log.info(f"{source_name}: резолвлю коды для {brand} {model} (не в кэше)")
    try:
        result = lookup_fn(brand, model)
    except Exception as error:
        log.error(f"{source_name}: ошибка резолвинга {brand} {model}: {error}")
        result = None

    cache[key] = {"result": result, "resolved_at": datetime.now().isoformat(timespec="seconds")}
    _save_cache(cache)

    if result is None:
        log.warning(f"{source_name}: не удалось найти {brand} {model} на сайте")
    else:
        log.info(f"{source_name}: {brand} {model} -> {result}")

    return result
