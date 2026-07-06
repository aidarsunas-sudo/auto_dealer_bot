# -*- coding: utf-8 -*-
"""
target_collector.py — сбор объявлений по target_models.json через 4
источника без логина (kbchachacha, encar, charancha, onbid).

Используется ТОЛЬКО main_pipeline.py. orchestrator.py (автоматика по
расписанию для WATCHLIST/USED_CAR_SEARCHES) target_models.json больше не
читает — иначе одна и та же целевая модель публиковалась бы дважды: один раз
обзорным постом через orchestrator.py, второй раз лидогенерирующим постом
через main_pipeline.py.
"""

import logging

from target_model_resolver import load_target_models

import kbchachacha_target_adapter
import encar_scraper
import charancha_scraper
import onbid_scraper

NOLOGIN_TARGET_ADAPTERS = {
    "kbchachacha": kbchachacha_target_adapter.search_target_model,
    "encar": encar_scraper.search_target_model,
    "charancha": charancha_scraper.search_target_model,
    "onbid": onbid_scraper.search_target_model,
}


def collect_target_model_cars(log: logging.Logger) -> list:
    """
    Обходит target_models.json по всем 4 источникам без логина. Каждая пара
    (модель, источник) — в своём try/except, сбой одного источника не мешает
    собрать данные с остальных.

    Returns:
        Список словарей-машин в формате, совместимом с database.save_car()
        (+ доп. поля для отчётов, которые save_car просто не читает).
    """
    all_cars = []

    target_models = load_target_models()
    for target in target_models:
        # target_models.json редактируется вручную — опечатка (например,
        # пропущенное поле "model") не должна ронять ВЕСЬ прогон
        # main_pipeline.py (раньше падало необработанным KeyError ещё до
        # try/except ниже — теряя и остальные target_models.json записи,
        # и broad-режим, который к этому моменту ещё даже не запускался).
        if "brand" not in target or "model" not in target:
            log.error(f"target_collector: запись в target_models.json без обязательного "
                      f"поля brand/model — пропускаю: {target}")
            continue

        for source_name, search_fn in NOLOGIN_TARGET_ADAPTERS.items():
            log.info(f"── Целевая модель: {target['brand']} {target['model']} ({source_name}) ──")
            try:
                cars = search_fn(target, log)
            except Exception as error:
                log.error(f"target_collector: {source_name} пропущен для {target['brand']} "
                          f"{target['model']} ({error})")
                continue
            all_cars.extend(cars)

    return all_cars
