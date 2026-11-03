# -*- coding: utf-8 -*-
"""
lead_post_builder.py — генератор лидогенерирующего поста для main_pipeline.py/
publish_now.py. С 2026-10-07 — тонкая обёртка над post_render.render_post(lot, "lead"):
прежний lead_post_template.txt перенесён в единый шаблон (режимы lead/auction),
здесь остался только прежний контракт «текст или None».

Поля и обязательные проверки (хангыль в названии, нет фото, не хватает данных для
цены) — те же: prepare_post_fields() в post_text_builder.py, общая для всех
сценариев публикации. OnBid показывает СТАРТОВУЮ цену аукциона, не гарантированную —
эта оговорка к цене живёт в post_render.py (ONBID_PRICE_NOTE).

ВАЖНО: car["url"] (ссылка на объявление-источник) в текст НЕ подставляется намеренно —
площадка-источник должна остаться незаметной; url нужен только как ключ дедупа
(published_log.py, registry.py).
"""

import logging

from post_render import PostRenderError, render_post

logger = logging.getLogger("lead_post_builder")


def build_lead_post(car: dict, rates: dict) -> str | None:
    """
    Возвращает готовый текст поста, или None, если пост создавать нельзя —
    не хватает данных для честного расчёта цены, нет читаемого названия, нет фото
    или текст не прошёл формат канала (см. post_render.render_post()).
    """
    try:
        return render_post(car, "lead", rates).text
    except PostRenderError as error:
        logger.info(f"lead_post_builder: пропущено ({error}) — "
                    f"{car.get('brand', '')} {car.get('model', '')}")
        return None
