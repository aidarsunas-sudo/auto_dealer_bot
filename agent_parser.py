# -*- coding: utf-8 -*-
"""
agent_parser.py — "агент-парсер" в мультиагентном пайплайне (orchestrator.py).

Это тонкая обёртка над уже существующими scraper.py и used_car_scraper.py —
никакой новой логики скачивания/разбора здесь нет. Задача этого модуля —
собрать машины из ВСЕХ источников (WATCHLIST + USED_CAR_SEARCHES) в один
плоский список, готовый к сохранению через database.save_car(), и сделать
это так, чтобы падение одного источника не мешало собрать данные с остальных.

target_models.json сюда НЕ входит — с переходом на main_pipeline.py он стал
исключительно его территорией (см. target_collector.py), чтобы одна и та же
машина не публиковалась дважды (один раз через orchestrator.py, второй —
через main_pipeline.py).
"""

import logging

from scraper import fetch_html, parse_car_page
from used_car_scraper import parse_all_pages as parse_used_cars


def collect_all_cars(watchlist: list, used_car_searches: list, log: logging.Logger) -> list:
    """
    Обходит WATCHLIST (новые авто у дилеров) и USED_CAR_SEARCHES (б/у авто),
    возвращает единый список машин в формате, совместимом с database.save_car().

    Каждый источник обрабатывается в своём try/except — сбой одной страницы
    или одного поиска не останавливает сбор данных с остальных источников.

    Returns:
        Список словарей-машин (может быть пустым, если вообще ничего не
        удалось собрать — это не ошибка сама по себе, вызывающий код просто
        не найдёт, что обрабатывать дальше).
    """
    all_cars = []

    for url, brand, model in watchlist:
        log.info(f"── Собираю: {brand} {model} ──")
        try:
            html = fetch_html(url)
            cars = parse_car_page(html, url, brand, model)
        except Exception as error:
            log.error(f"agent_parser: пропускаю {brand} {model}, не удалось собрать ({error})")
            continue

        if not cars:
            log.warning(f"agent_parser: на странице {brand} {model} ничего не найдено")
            continue

        all_cars.extend(cars)

    for search_params in used_car_searches:
        log.info(f"── Собираю б/у авто: {search_params} ──")
        try:
            cars = parse_used_cars(search_params)
        except Exception as error:
            log.error(f"agent_parser: пропускаю поиск {search_params}, не удалось собрать ({error})")
            continue

        if not cars:
            log.warning(f"agent_parser: по запросу {search_params} ничего не найдено")
            continue

        all_cars.extend(cars)

    return all_cars
