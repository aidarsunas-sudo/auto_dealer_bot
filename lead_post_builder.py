# -*- coding: utf-8 -*-
"""
lead_post_builder.py — генератор лидогенерирующего поста для main_pipeline.py/
publish_now.py. Тонкая обёртка над post_text_builder.prepare_post_fields()
(единая функция подготовки полей и обязательных проверок — см. её докстринг)
— здесь только СВОЙ текстовый шаблон (lead_post_template.txt) и одно
специфичное поле (price_label — OnBid показывает стартовую цену аукциона,
не гарантированную).

В отличие от price_alert_builder.build_price_alert_post() (обзорный тон "у
машины, которую вы уже видели, что-то изменилось") — здесь "новая машина,
которую можно заказать". Разный смысл для подписчика, поэтому разные .txt
шаблоны — но НЕ разная логика сборки полей/проверок, она общая (см.
post_text_builder.py).
"""

import logging
from pathlib import Path

from post_text_builder import prepare_post_fields

logger = logging.getLogger("lead_post_builder")

TEMPLATE_PATH = Path(__file__).resolve().parent / "lead_post_template.txt"


def build_lead_post(car: dict, rates: dict) -> str | None:
    """
    Возвращает готовый текст поста, или None, если пост создавать нельзя —
    не хватает данных для честного расчёта цены, нет читаемого названия или
    нет фото (см. post_text_builder.prepare_post_fields() — общие для ВСЕХ
    сценариев публикации блокирующие проверки).
    """
    fields, reason = prepare_post_fields(car, rates)
    if fields is None:
        logger.info(f"lead_post_builder: пропущено ({reason}) — "
                    f"{car.get('brand', '')} {car.get('model', '')}")
        return None

    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as error:
        logger.error(f"Не удалось прочитать {TEMPLATE_PATH.name}: {error}")
        return None

    # OnBid — гос. площадка АУКЦИОНОВ: car["price"] там — стартовая цена
    # торгов (lowstBidPrc), не гарантированная цена покупки — итоговая
    # цена по факту торгов может быть выше. Честно показываем это в посте,
    # а не выдаём стартовую цену за окончательную.
    if car.get("source") == "onbid":
        price_label = "Стартовая цена аукциона под ключ в Казани (итоговая может быть выше)"
    else:
        price_label = "Стоимость под ключ в Казани"

    fields = dict(fields)
    fields["price_label"] = price_label
    # ВАЖНО: car["url"] (ссылка на объявление-источник) сюда НЕ
    # подставляется намеренно — пользователю в посте она видна быть не
    # должна (площадка-источник должна остаться незаметной). url
    # используется только внутри published_log.py как ключ дедупа.

    try:
        return template.format(**fields)
    except KeyError as error:
        logger.error(f"lead_post_template.txt содержит неизвестный плейсхолдер: {error}")
        return None
