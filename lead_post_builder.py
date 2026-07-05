# -*- coding: utf-8 -*-
"""
lead_post_builder.py — генератор лидогенерирующего поста для main_pipeline.py.

В отличие от post_builder.build_telegram_post() (обзорный тон, для
orchestrator.py/WATCHLIST), здесь текст — из ВНЕШНЕГО редактируемого файла
lead_post_template.txt (плейсхолдеры вида {price_krw}), чтобы менять тон и
формулировки без правки Python-кода. Цифры считает та же общая функция
post_builder.calculate_price_details() — чтобы цифры не расходились между
разными типами постов.
"""

import logging
from pathlib import Path

import config
from post_builder import calculate_price_details

logger = logging.getLogger("lead_post_builder")

TEMPLATE_PATH = Path(__file__).resolve().parent / "lead_post_template.txt"
CTA_BOT = "@korea_auto_kazan_bot"


def _format_number(value) -> str:
    if value is None:
        return "уточняется"
    return f"{int(value):,}".replace(",", " ")


def build_lead_post(car: dict, rates: dict) -> str | None:
    """
    Возвращает готовый текст поста, или None, если данных не хватает для
    расчёта точной цены (лидогенерирующий пост без честной цифры "под ключ"
    не имеет смысла — в отличие от build_telegram_post, здесь не откатываемся
    на "цена уточняется").
    """
    details = calculate_price_details(car, rates)
    if details is None:
        logger.info(f"lead_post_builder: не хватает данных для {car.get('brand')} "
                    f"{car.get('model')} — пропускаю")
        return None

    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as error:
        logger.error(f"Не удалось прочитать {TEMPLATE_PATH.name}: {error}")
        return None

    fields = {
        "brand": car.get("brand", ""),
        "model": car.get("model", ""),
        "trim": car.get("trim", ""),
        "year": car.get("year") or "не указан",
        "mileage_km": f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "новый",
        "specs": car.get("specs", ""),
        "price_krw": f"{_format_number(car.get('price'))} ₩",
        "car_price_rub": _format_number(details["car_price_rub"]),
        "bank_commission_rub": _format_number(details["bank_commission_rub"]),
        "customs_rub": _format_number(details["customs_rub"]),
        "extra_costs_rub": _format_number(details["extra_costs_rub"]),
        "total_rub": _format_number(details["total_rub"]),
        "url": car.get("url", ""),
        "cta_bot": CTA_BOT,
    }

    try:
        return template.format(**fields)
    except KeyError as error:
        logger.error(f"lead_post_template.txt содержит неизвестный плейсхолдер: {error}")
        return None
