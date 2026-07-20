# -*- coding: utf-8 -*-
"""
yandex_post_builder.py — генератор текста для Яндекс/Дзен-витрины (посты про
СВОИ лоты дилера: то, что реально куплено/заказано и продаётся, а не чужие
объявления с корейских площадок). ОТДЕЛЬНЫЙ поток от lead_post_builder.py/
price_alert_builder.py — см. CLAUDE.md, раздел "Контент-потоки": формат,
CTA и канал у лидогена зафиксированы и этим модулем не затрагиваются.

У Яндекс Карт нет API публикации (см. правило "Не выдумывать несуществующие
API" в CLAUDE.md) — build_yandex_post() только готовит текст, отправка
человеку на копирование — через yandex_publisher.send_yandex_draft().

Как и лидоген, использует post_text_builder.prepare_post_fields() — ту же
единую точку подготовки полей/обязательных проверок (фото, читаемое
название, расчёт цены), а не отдельную копию этой логики.

Статус лота (in_stock/under_order):
    По умолчанию ВСЕ лоты — under_order: дилер привозит машину из Кореи под
    заказ, это и есть услуга. in_stock — ИСКЛЮЧЕНИЕ, ставится вручную через
    own_stock.json (список внешних id лотов, которые физически уже в Казани)
    — см. resolve_stock_status().

Шаблоны (утверждены пользователем построчно, включая формулировки CTA и
состав цены) — менять текст здесь можно только по прямому запросу, не
"улучшая" на своё усмотрение, см. CLAUDE.md про инциденты с расхождением
форматов.
"""

import json
import logging
import re
from pathlib import Path

import config
from post_text_builder import prepare_post_fields

logger = logging.getLogger("yandex_post_builder")

OWN_STOCK_PATH = Path(__file__).resolve().parent / "own_stock.json"

# "ПТС" -> "ЭПТС": с 2020 года новые ПТС выдаются только в электронном виде
# (см. CLAUDE.md) — везде, где текст мог случайно получить старое "ПТС"
# (шаблон ниже уже пишет "ЭПТС" напрямую, это доп. защита на будущее).
_PTS_RE = re.compile(r"(?<![А-Яа-яЁё])ПТС(?![А-Яа-яЁё])")

CTA_CONSULTATION = "Записаться на консультацию — по телефону или сообщением в этом профиле."
CTA_VIEWING = "Записаться на просмотр — по телефону или сообщением в этом профиле."

# {mileage_km}/{total_rub} приходят уже готовыми точными числами-строками из
# post_text_builder.prepare_post_fields() ("45 000 км", "2 350 000") — без
# "до"/диапазонов, см. yandex_post_validator.py про то, почему это важно.
# Год выпуска обязателен и в заголовке, и в первой строке текста (см.
# yandex_post_validator._YEAR_RE) — блокирующая проверка, не только стиль:
# на витрине покупателю год не менее важен, чем пробег/цена.
UNDER_ORDER_TITLE_TEMPLATE = "{title} {year}, {mileage_km}, под заказ"
UNDER_ORDER_TEXT_TEMPLATE = (
    "{title} {year} — привезём под заказ с корейского аукциона.\n"
    "Двигатель {engine_display}, пробег {mileage_km}. "
    "Комплектация: {specs}.\n"
    "Оформление под ключ в Казани, ориентировочная цена: {total_rub} ₽ — "
    "в цену уже входят выкуп, доставка, растаможка и оформление ЭПТС.\n"
    "Срок поставки — {delivery_days} дней.\n"
    f"{CTA_CONSULTATION}"
)

IN_STOCK_TITLE_TEMPLATE = "{title} {year}, {mileage_km}, в наличии"
IN_STOCK_TEXT_TEMPLATE = (
    "{title} {year}, двигатель {engine_display}, пробег {mileage_km}.\n"
    "Автомобиль в наличии в Казани — привезён с корейского аукциона, "
    "растаможен, ЭПТС оформлен. Комплектация: {specs}.\n"
    "Цена под ключ в Казани: {total_rub} ₽.\n"
    f"{CTA_VIEWING}"
)


def _replace_pts(text: str) -> str:
    return _PTS_RE.sub("ЭПТС", text)


def _load_own_stock_ids() -> set:
    try:
        raw = OWN_STOCK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        logger.error(f"own_stock.json повреждён ({error}) — считаю список пустым, "
                     f"все лоты будут under_order")
        return set()
    return set(data)


def resolve_stock_status(car: dict) -> str:
    """
    'in_stock', если external_id (или url, если id нет) лота найден в
    own_stock.json, иначе 'under_order' (дефолт — см. докстринг файла).
    """
    identifier = car.get("external_id") or car.get("url")
    if not identifier:
        return "under_order"
    return "in_stock" if identifier in _load_own_stock_ids() else "under_order"


def build_yandex_post(car: dict, rates: dict, stock_status: str | None = None) -> tuple[dict | None, str]:
    """
    Args:
        car: тот же словарь объявления, что у lead_post_builder/price_alert_builder.
        rates: курсы валют (rates.fetch_cbr_rates()).
        stock_status: 'in_stock'/'under_order' — принудительно, вместо
            resolve_stock_status(car). None (по умолчанию) — определить
            автоматически по own_stock.json.

    Returns:
        ({"title":.., "text":.., "stock_status":..}, "") — можно валидировать
            (см. yandex_post_validator.validate_yandex_post()) и отправлять.
        (None, reason) — собрать пост нельзя (нет фото/названия/цены —
            те же блокирующие проверки, что и у остальных builder'ов, см.
            post_text_builder.prepare_post_fields()).
    """
    fields, reason = prepare_post_fields(car, rates)
    if fields is None:
        return None, reason

    status = stock_status or resolve_stock_status(car)
    if status not in ("in_stock", "under_order"):
        return None, f"неизвестный статус лота: {status!r}"

    template_fields = dict(
        fields,
        specs=fields.get("specs") or "уточняется по лоту",
        delivery_days=f"{config.DELIVERY_DAYS_MIN}–{config.DELIVERY_DAYS_MAX}",
    )

    if status == "in_stock":
        title = IN_STOCK_TITLE_TEMPLATE.format(**template_fields)
        text = IN_STOCK_TEXT_TEMPLATE.format(**template_fields)
    else:
        title = UNDER_ORDER_TITLE_TEMPLATE.format(**template_fields)
        text = UNDER_ORDER_TEXT_TEMPLATE.format(**template_fields)

    return {
        "title": _replace_pts(title),
        "text": _replace_pts(text),
        "stock_status": status,
        # Не используются в самом тексте — нужны yandex_post_validator.py
        # для проверки hp против model_specs.model_has_privileged_hp_variant().
        "brand": car.get("brand", ""),
        "model": car.get("model", ""),
        "hp": car.get("hp"),
    }, ""
