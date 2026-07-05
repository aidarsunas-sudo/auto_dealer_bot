# -*- coding: utf-8 -*-
"""
broad_search_config.py — настройки широкого поиска (Режим 2, см.
broad_collector.py): "любые модели с hp<=max_hp", без привязки к конкретным
брендам из target_models.json. Пользователь редактирует
broad_search_config.json вручную — так же, как target_models.json.

Поля:
    max_hp:                верхний порог мощности (л.с.) — 160 = порог
                            льготного утильсбора для физлиц (см.
                            customs_calculator.util_calc). Реальная проверка
                            всё равно идёт по hp из model_specs.find_spec()
                            (ни один из 4 источников не отдаёт л.с. напрямую,
                            см. разбор в чате) — параметр здесь используется
                            как постфильтр ПОСЛЕ того, как hp определён.
    max_pages_per_source:   сколько страниц (сортировка "сначала новые")
                            просматривать на каждом источнике за один
                            прогон — весь рынок целиком просматривать
                            нереально (десятки-сотни тысяч объявлений).
    fuel_types:             список кодов топлива ("diesel"/"petrol"/...)
                            для фильтрации, или null — без ограничения.
    year_from:               минимальный год выпуска, или null.
    mileage_max:             максимальный пробег (км), или null.
"""

import json
import logging
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "broad_search_config.json"

logger = logging.getLogger("broad_search_config")

DEFAULTS = {
    "max_hp": 160,
    "max_pages_per_source": 5,
    "fuel_types": None,
    "year_from": None,
    "mileage_max": None,
}


def load_broad_search_config() -> dict:
    """
    Загружает broad_search_config.json. Отсутствие файла — не ошибка,
    возвращаются безопасные дефолты (см. DEFAULTS).
    """
    if not CONFIG_PATH.exists():
        logger.info(f"{CONFIG_PATH.name} не найден — использую дефолты: {DEFAULTS}")
        return dict(DEFAULTS)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось разобрать {CONFIG_PATH.name}: {error} — использую дефолты")
        return dict(DEFAULTS)

    return {**DEFAULTS, **data}
