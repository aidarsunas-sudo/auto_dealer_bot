# -*- coding: utf-8 -*-
"""
price_alert_builder.py — генератор поста-уведомления об изменении цены/новом
поступлении для main.py (WATCHLIST/USED_CAR_SEARCHES — мониторинг цен у
дилеров и б/у объявлений, НЕ лидогенерация по target_models.json).

Отдельно от lead_post_builder.py (main_pipeline.py) намеренно — разный смысл
для подписчика: там "новая машина, которую можно заказать", здесь "у уже
известной вам машины что-то изменилось". Общие технические кусочки (перевод
КПП, сборка заголовка, форматирование чисел) переиспользуются из
lead_post_builder.py, чтобы не дублировать код.

Если hp не резолвился через model_specs.py — НЕ публикуем и НЕ откатываемся
на старый шаблон (post_builder.build_telegram_post), а логируем в
unknown_watchlist_models.json для последующего ручного добавления через
add_model.py. См. обсуждение: откат на старый формат создавал бы
непредсказуемую смесь старого/нового стиля постов в одном канале.
"""

import logging
from pathlib import Path

from post_builder import calculate_price_details
from lead_post_builder import _format_number, _build_title, _translate_transmission, CTA_BOT
from unknown_watchlist_models import log_unknown_model

logger = logging.getLogger("price_alert_builder")

TEMPLATE_PATH = Path(__file__).resolve().parent / "price_alert_template.txt"

STATUS_WORDS = {
    "new": "Новое поступление",
    "updated": "Цена изменилась",
}


def build_price_alert_post(car: dict, status: str, rates: dict) -> str | None:
    """
    Возвращает готовый текст поста, или None, если данных не хватает для
    честного расчёта цены — в частности, если hp не резолвился через
    model_specs.py (тогда дополнительно логирует в
    unknown_watchlist_models.json, чтобы можно было добавить модель вручную).
    """
    details = calculate_price_details(car, rates)
    if details is None:
        if car.get("hp") is None and car.get("price"):
            # Цена есть, а hp — нет: это именно "не нашли спеку", а не общая
            # нехватка данных (например, не спарсилась цена вообще) —
            # логируем только этот случай, он лечится через add_model.py.
            raw_title = car.get("model") or car.get("trim") or ""
            log_unknown_model(raw_title, car.get("brand", ""), car.get("model", ""),
                               car.get("source", "watchlist"))
            logger.info(f"price_alert_builder: hp не резолвился для {raw_title!r} — "
                        f"залогировано в unknown_watchlist_models.json, не публикую")
        else:
            logger.info(f"price_alert_builder: не хватает данных для {car.get('brand')} "
                        f"{car.get('model')} — пропускаю")
        return None

    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as error:
        logger.error(f"Не удалось прочитать {TEMPLATE_PATH.name}: {error}")
        return None

    cc = car.get("cc")
    hp = car.get("hp")
    engine_display = f"{cc / 1000:.1f} л, {hp} л.с." if cc and hp else "не указан"
    status_word = STATUS_WORDS.get(status, "Обновление")

    fields = {
        "title": _build_title(car),
        "year": car.get("year") or "не указан",
        "mileage_km": f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "новый",
        "engine_display": engine_display,
        "transmission": _translate_transmission(car.get("transmission")),
        "total_rub": _format_number(details["total_rub"]),
        "status_word": status_word,
        "cta_bot": CTA_BOT,
    }

    try:
        return template.format(**fields)
    except KeyError as error:
        logger.error(f"price_alert_template.txt содержит неизвестный плейсхолдер: {error}")
        return None
