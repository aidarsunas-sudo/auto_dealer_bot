# -*- coding: utf-8 -*-
"""
post_text_builder.py — ЕДИНАЯ функция подготовки полей поста, используемая
АБСОЛЮТНО ВСЕМИ сценариями публикации: первичная публикация (lead_post_builder.
build_lead_post — main_pipeline.py, publish_now.py) и обновление цены
(price_alert_builder.build_price_alert_post — main.py, "Цена изменилась").

Инцидент 2026-07: одна и та же группа багов (корейский текст в названии,
"КПП не указано", отсутствие фото) возвращалась несколько раз, потому что
"Цена изменилась" — ОТДЕЛЬНЫЙ путь кода (main.py вызывает
used_car_scraper.parse_all_pages() НАПРЯМУЮ, минуя kbchachacha_target_adapter.py,
куда вписывались прошлые фиксы) — фикс, сделанный в одном builder-модуле, не
попадал в другой. Корень исправлен здесь и в used_car_scraper.py (см. его
докстринг) — но чтобы это не могло повториться СТРУКТУРНО, вся логика сборки
полей поста (расчёт цены, заголовок, перевод КПП, форматирование чисел) и
все ОБЯЗАТЕЛЬНЫЕ проверки перед тем, как пост вообще будет создан, лежат
ЗДЕСЬ и только здесь. lead_post_builder.py/price_alert_builder.py — тонкие
обёртки (свой .txt шаблон + пара специфичных полей: price_label/status_word),
не более того.

БЛОКИРУЮЩИЕ проверки (пост НЕ создаётся вообще — prepare_post_fields()
возвращает (None, reason), а не текст "как получится"):
    1. Название (brand+model+trim) — человекочитаемое: та же проверка на
       хангыль/латиницу-кириллицу, что и в post_validator.py (HANGUL_RE/
       ALLOWED_TITLE_RE оттуда — один источник правды на regex, не два).
    2. Хотя бы 1 фото. ВАЖНО: это НЕ то же самое, что config.MIN_PHOTOS_PER_POST
       (порог КАЧЕСТВА перед публикацией, см. post_validator.py — там
       минимум 3, финальный гейт прямо перед HTTP-запросом в
       agent_publisher.publish_post()) — здесь более ранний, мягкий порог
       "вообще есть смысл считать цену и собирать текст" — тот же принцип
       двух независимых рубежей защиты, что уже применён для года выпуска
       (filter_year_range на этапе парсинга + validate_post_before_publish
       перед публикацией).
    3. Расчёт цены под ключ должен получиться (calculate_price_details()
       не вернул None — не хватает price/cc/hp/fuel_code).

КПП (transmission): "не указано" — для источников, которые не отдают
трансмиссию (kbchachacha/OnBid — HTML поиска не содержит этих данных).
Encar (поле "Transmission") и Charancha (поле "transmissionName") — отдают.
"""

import logging
import re

from brand_names_ko import translate_trims
from post_builder import calculate_price_details

logger = logging.getLogger("post_text_builder")

CTA_BOT = "@korea_auto_kazan_bot"

# Хангыль (корейский алфавит): слоговые блоки 가-힣 + отдельные буквы чамо
# (изначальный блок Hangul Jamo) ᄀ-ᇿ. Канонический источник — здесь (не в
# post_validator.py): post_validator.validate_post_before_publish() ПОВТОРНО
# использует ЭТИ ЖЕ константы (импортирует их отсюда) для финальной проверки
# перед публикацией — один источник правды на паттерн, две независимые
# точки проверки (на построении текста и перед отправкой).
HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ]")

# Разрешено в названии: латиница, кириллица, цифры, пробелы и стандартная
# пунктуация, реально встречающаяся в человекочитаемых label из model_specs.py
# (проверено вживую на всех 21 текущих записях — напр. "BMW X6 xDrive30d
# (G06 LCI, рестайлинг 2023+)", "Kia K5 (DL3) 2.0 Gasoline (Smartstream
# G2.0 MPi)" — скобки/запятая/плюс там не редкость, не экзотика).
ALLOWED_TITLE_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 \-.,()+/]+$")

# Charancha отдаёт transmissionName по-корейски ("자동"/"수동") — переводим
# для показа в посте; CVT уже латиницей на самой площадке, перевод не нужен.
_TRANSMISSION_RU = {
    "자동": "автомат",
    "오토": "автомат",
    "수동": "механика",
}


def _translate_transmission(value) -> str:
    if not value:
        return "не указано"
    return _TRANSMISSION_RU.get(value, value)


def _format_number(value) -> str:
    if value is None:
        return "уточняется"
    return f"{int(value):,}".replace(",", " ")


def _build_hashtags(car: dict) -> str:
    """
    3-5 хэштегов в конец поста (TG/VK) — #автоизкореи/#казань постоянные,
    плюс марка+модель одним слитным тегом. #подзаказ везде без исключений:
    весь лидоген (lead_post_builder.py/price_alert_builder.py) — это ЧУЖИЕ
    объявления с корейских площадок, которые можно заказать, а не собственный
    склад дилера — own_stock.json (Яндекс-витрина, см. yandex_post_builder.py)
    к этим объявлениям отношения не имеет, поэтому #вналичии здесь не бывает.
    """
    brand = (car.get("brand") or "").strip()
    model = (car.get("model") or "").strip()
    model_tag = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", f"{brand}{model}").lower()
    tags = ["#автоизкореи", "#подзаказ", "#казань"]
    if model_tag:
        tags.append(f"#{model_tag}")
    return " ".join(tags)


def _build_title(car: dict) -> str:
    """
    Собирает заголовок из brand/model/trim, отбрасывая пустые и повторяющиеся
    куски. Нужно из-за двух разных сценариев в normalized-объекте:
    - targeted-режим (target_models.json): brand/model — канонические
      (Audi/Q3), trim — описание с сайта. Раздельные, не пересекаются.
    - broad-режим (без target): у части источников (onbid, kbchachacha)
      brand пустой, а model/trim могут быть тем же самым сырым текстом с
      сайта целиком — без дедупа заголовок дублировался бы или показывал
      "  модель trim" с лишним пробелом от пустого brand.
    """
    parts = []
    for value in (car.get("brand"), car.get("model"), car.get("trim")):
        # Корейские названия комплектаций (노블레스, 시그니처…) -> латиница, см.
        # brand_names_ko.TRIM_RU; неизвестные остаются и логируются (лот отсеется гейтом).
        value = translate_trims((value or "").strip())
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def prepare_post_fields(car: dict, rates: dict) -> tuple[dict | None, str]:
    """
    Готовит ВСЕ общие поля поста (title/year/mileage_km/specs/engine_display/
    transmission/цифры цены) и прогоняет обязательные блокирующие проверки
    (см. докстринг файла).

    Args:
        car: словарь объявления (brand/model/trim/year/mileage_km/cc/hp/
             fuel_code/price/photos/transmission/source/...).
        rates: курсы валют (rates.fetch_cbr_rates()).

    Returns:
        (fields, "") — можно строить текст, fields готов для
            template.format(**fields, **свои_доп_поля).
        (None, reason) — пост НЕ создаётся вообще; reason — человекочитаемая
            причина для лога вида "пропущено: {reason}".
    """
    title = _build_title(car)
    if not title:
        return None, "нет читаемого названия (пустой заголовок)"
    if HANGUL_RE.search(title):
        return None, f"нет читаемого названия (корейский текст: {title!r})"
    if not ALLOWED_TITLE_RE.match(title):
        return None, f"нет читаемого названия (недопустимые символы: {title!r})"

    photos = car.get("photos") or []
    if len(photos) < 1:
        return None, "нет фото"

    details = calculate_price_details(car, rates)
    if details is None:
        return None, "не хватает данных для честного расчёта цены под ключ"

    cc = car.get("cc")
    hp = car.get("hp")
    engine_display = f"{cc / 1000:.1f} л, {hp} л.с." if cc and hp else "не указан"

    fields = {
        "title": title,
        "brand": car.get("brand", ""),
        "model": car.get("model", ""),
        "trim": car.get("trim", ""),
        "year": car.get("year") or "не указан",
        "mileage_km": f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "новый",
        "specs": car.get("specs", ""),
        "engine_display": engine_display,
        # Не все источники отдают трансмиссию (Charancha — да, Encar/
        # kbchachacha/OnBid — нет без доп. запроса к детальной странице) —
        # честно показываем "не указано", а не гадаем (не баг, см. докстринг).
        "transmission": _translate_transmission(car.get("transmission")),
        "price_krw": f"{_format_number(car.get('price'))} ₩",
        "car_price_rub": _format_number(details["car_price_rub"]),
        "bank_commission_rub": _format_number(details["bank_commission_rub"]),
        "customs_rub": _format_number(details["customs_rub"]),
        "extra_costs_rub": _format_number(details["extra_costs_rub"]),
        "total_rub": _format_number(details["total_rub"]),
        # Целое число (не отформатированная строка выше) — нужно
        # post_validator.check_price_absolute()/check_price_relative() для
        # сравнения с config.PRICE_MIN_RUB/PRICE_MAX_RUB/ModelSpec.price_range_rub.
        # Не влияет на template.format(**fields) — лишний неиспользуемый
        # ключ в словаре шаблону не мешает.
        "total_rub_raw": details["total_rub"],
        "cta_bot": CTA_BOT,
        "hashtags": _build_hashtags(car),
    }
    return fields, ""
