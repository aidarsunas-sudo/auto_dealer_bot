# -*- coding: utf-8 -*-
"""
post_builder.py — calculate_price_details(): общий расчёт цены "под ключ
Казань", используемый ОБОИМИ активными генераторами постов —
lead_post_builder.py (main_pipeline.py) и price_alert_builder.py (main.py) —
чтобы цифры не расходились между разными типами постов.

С 2026-10-07 сама формула живёт в pricing.py (параметры — в config.py, ставки
пошлин — в customs_rates.py), здесь осталась тонкая обёртка со старым именем и
старым форматом словаря: все прежние вызовы работают без изменений.

Раньше здесь же был build_telegram_post() — собственный шаблонный генератор
текста поста (со ссылкой на источник и построчной разбивкой расходов).
Убран как мёртвый код при аудите 2026-07-06: ни один активный путь публикации
его больше не вызывает (main.py/main_pipeline.py используют price_alert_builder.py/
lead_post_builder.py — единый компактный формат, см. post_format_schema.py).
"""

from pricing import calculate_price


def calculate_price_details(car: dict, rates: dict) -> dict | None:
    """
    Считает точную цену "под ключ Казань" для машины — общая функция для
    ЛЮБОГО генератора поста, чтобы все показывали ОДИНАКОВЫЕ, честные цифры.

    Args:
        car: словарь с полями price, cc, hp, fuel_code, year, month (см.
             scraper.parse_car_page() / used_car_scraper.parse_used_car_list()).
             month — необязателен (не все источники его дают, см.
             age_calculator.py про консервативный дефолт при отсутствии).
        rates: курсы валют (см. rates.fetch_cbr_rates()).

    Returns:
        None, если данных не хватает для расчёта (нет цены/объёма/
        мощности/топлива) — тогда вызывающий код должен показать
        "не хватает данных", а не гадать.
        Иначе — словарь (см. pricing.PriceResult.as_legacy_dict()):
            total_rub, car_price_rub (с корейской надбавкой), bank_commission_rub,
            customs_rub (сбор+ЕТС+утильсбор), extra_costs_rub (логистика+брокер+наценка),
            exchange_spread_rub / korea_costs_rub (надбавка), car_price_cbr_rub,
            fee_rub, duty_rub, util_rub, util_lgota, util_coeff, age_bucket,
            warnings — список предупреждений (утильсбор не льготный, запасные курсы и т.п.).
    """
    result = calculate_price(car, rates)
    return result.as_legacy_dict() if result else None
