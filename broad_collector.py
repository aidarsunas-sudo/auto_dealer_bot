# -*- coding: utf-8 -*-
"""
broad_collector.py — Режим 2 ("широкий поиск"): любые модели с
hp<=max_hp (порог льготного утильсбора для физлиц), без привязки к
конкретным брендам/моделям из target_models.json.

В отличие от target_collector.py — резолвер/кэш здесь не нужен (марка не
фиксирована заранее), поэтому просто вызываем browse_broad() у каждого
источника напрямую.

Используется ТОЛЬКО main_pipeline.py (см. SEARCH_MODE в config.py) — те же
4 источника без логина, что и в target_collector.py.
"""

import logging

from broad_search_config import load_rotating_segment
from post_validator import filter_year_range

import kbchachacha_target_adapter
import encar_scraper
import charancha_scraper
import onbid_scraper

BROAD_ADAPTERS = {
    "kbchachacha": kbchachacha_target_adapter.browse_broad,
    "encar": encar_scraper.browse_broad,
    "charancha": charancha_scraper.browse_broad,
    "onbid": onbid_scraper.browse_broad,
}


def collect_broad_cars(log: logging.Logger) -> list:
    """
    Обходит все 4 источника без логина в широком режиме (см.
    broad_search_config.json). Каждый источник — в своём try/except, сбой
    одного не мешает собрать данные с остальных.

    Returns:
        Список словарей-машин в формате, совместимом с database.save_car()
        (+ доп. поля для отчётов) — все с подтверждённым hp<=max_hp.
    """
    config = load_rotating_segment(log)
    all_cars = []

    for source_name, browse_fn in BROAD_ADAPTERS.items():
        log.info(f"── Широкий поиск: {source_name} (hp в [{config.get('min_hp') or 0}, {config['max_hp']}]) ──")
        try:
            cars = browse_fn(config, log)
        except Exception as error:
            log.error(f"broad_collector: {source_name} пропущен ({error})")
            continue
        all_cars.extend(cars)

    # Жёсткий диапазон года (config.MIN_YEAR/MAX_YEAR) — сразу после сбора,
    # безусловно, независимо от broad_search_config.json["year_from"]
    # (который необязателен и не проверяет верхнюю границу) — см. post_validator.py.
    return filter_year_range(all_cars, log, "broad_collector")
