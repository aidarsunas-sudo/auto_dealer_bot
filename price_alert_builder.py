# -*- coding: utf-8 -*-
"""
price_alert_builder.py — генератор поста-уведомления об изменении цены/новом
поступлении для main.py (WATCHLIST/USED_CAR_SEARCHES — мониторинг цен у
дилеров и б/у объявлений, НЕ лидогенерация по target_models.json).

Отдельно от lead_post_builder.py (main_pipeline.py) намеренно — разный смысл
для подписчика: там "новая машина, которую можно заказать", здесь "у уже
известной вам машины что-то изменилось". Разные .txt шаблоны — но НЕ разная
логика сборки полей/обязательных проверок: она ОБЩАЯ для обоих builder'ов,
см. post_text_builder.prepare_post_fields() (единая функция, которую
использует ВЕСЬ проект без исключений — см. её докстринг про инцидент
2026-07, когда именно расхождение между этим модулем и lead_post_builder.py
позволило корейскому тексту/отсутствию фото уйти в канал).

Если hp не резолвился через model_specs.py — НЕ публикуем и НЕ откатываемся
на старый шаблон (post_builder.build_telegram_post), а логируем в
unknown_watchlist_models.json для последующего ручного добавления через
add_model.py. См. обсуждение: откат на старый формат создавал бы
непредсказуемую смесь старого/нового стиля постов в одном канале.
"""

import logging
from pathlib import Path

from post_text_builder import prepare_post_fields
from unknown_watchlist_models import log_unknown_model

logger = logging.getLogger("price_alert_builder")

TEMPLATE_PATH = Path(__file__).resolve().parent / "price_alert_template.txt"

STATUS_WORDS = {
    "new": "Новое поступление",
    "updated": "Цена изменилась",
}


def build_price_alert_post(car: dict, status: str, rates: dict) -> str | None:
    """
    Возвращает готовый текст поста, или None, если пост создавать нельзя —
    не хватает данных для честного расчёта цены, нет читаемого названия или
    нет фото (см. post_text_builder.prepare_post_fields() — общие для ВСЕХ
    сценариев публикации блокирующие проверки, включая этот, "Цена изменилась").
    """
    fields, reason = prepare_post_fields(car, rates)
    if fields is None:
        if car.get("hp") is None and car.get("price"):
            # Цена есть, а hp — нет: это именно "не нашли спеку", а не общая
            # нехватка данных — логируем только этот случай, он лечится
            # через add_model.py.
            raw_title = car.get("model") or car.get("trim") or ""
            log_unknown_model(raw_title, car.get("brand", ""), car.get("model", ""),
                               car.get("source", "watchlist"))
            logger.info(f"price_alert_builder: hp не резолвился для {raw_title!r} — "
                        f"залогировано в unknown_watchlist_models.json, не публикую")
        else:
            logger.info(f"price_alert_builder: пропущено ({reason}) — "
                        f"{car.get('brand', '')} {car.get('model', '')}")
        return None

    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as error:
        logger.error(f"Не удалось прочитать {TEMPLATE_PATH.name}: {error}")
        return None

    fields = dict(fields)
    fields["status_word"] = STATUS_WORDS.get(status, "Обновление")

    try:
        return template.format(**fields)
    except KeyError as error:
        logger.error(f"price_alert_template.txt содержит неизвестный плейсхолдер: {error}")
        return None
