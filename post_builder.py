# -*- coding: utf-8 -*-
"""
post_builder.py — calculate_price_details(): общий расчёт цены "под ключ
Казань", используемый ОБОИМИ активными генераторами постов —
lead_post_builder.py (main_pipeline.py) и price_alert_builder.py (main.py) —
чтобы цифры не расходились между разными типами постов.

Раньше здесь же был build_telegram_post() — собственный шаблонный генератор
текста поста (со ссылкой на источник и построчной разбивкой расходов).
Убран как мёртвый код при аудите 2026-07-06: ни один активный путь публикации
его больше не вызывает (main.py/main_pipeline.py используют price_alert_builder.py/
lead_post_builder.py — единый компактный формат, см. post_format_schema.py).
"""

import config
from customs_calculator import calc_full
from age_calculator import age_bucket_from_year, age_bucket_for_new_catalog_car


def calculate_price_details(car: dict, rates: dict) -> dict | None:
    """
    Считает точную цену "под ключ Казань" для машины — общая функция для
    ЛЮБОГО генератора поста (шаблонного здесь, или через Claude API в
    post_generator.py), чтобы оба варианта показывали ОДИНАКОВЫЕ, честные
    цифры, а не каждый считал по-своему.

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
        Иначе — словарь с результатом расчёта:
            {
                "total_rub": итоговая цена под ключ (целое число, рубли),
                "car_price_rub": цена самой машины в рублях,
                "bank_commission_rub": комиссия банка за перевод (% от car_price_rub),
                "customs_rub": пошлина+ЕТС+утильсбор суммарно,
                "extra_costs_rub": логистика+брокер+наценка суммарно,
                "age_bucket": использованная возрастная категория,
            }
    """
    price_krw = car.get("price")
    cc = car.get("cc")
    hp = car.get("hp")
    fuel_code = car.get("fuel_code")
    manufacture_year = car.get("year")
    manufacture_month = car.get("month")  # см. age_calculator.py — точность по месяцу

    if not all([price_krw, cc, hp, fuel_code]):
        return None

    # Возраст считаем по реальному году выпуска, если он известен (машина
    # с пробегом). Если года нет — это каталог НОВЫХ авто у дилера, и мы
    # точно знаем, что машина актуального модельного года.
    age = (age_bucket_from_year(manufacture_year, manufacture_month) if manufacture_year
           else age_bucket_for_new_catalog_car())

    result = calc_full(
        price=price_krw,
        currency="krw",
        cc=cc,
        power=hp,
        age=age,
        fuel=fuel_code,
        who=config.IMPORTER_TYPE,
        rates=rates,
    )

    # Банк.комиссия — только от цены самой машины (result["price_rub"]),
    # НЕ от таможни/утильсбора/доставки/брокера/наценки.
    bank_commission = result["price_rub"] * config.BANK_COMMISSION_PCT
    extra_costs = config.LOGISTICS_RUB + config.BROKER_FEE_RUB + config.MARGIN_RUB
    total_rub = int(result["total_with_car"] + bank_commission + extra_costs)

    return {
        "total_rub": total_rub,
        "car_price_rub": int(result["price_rub"]),
        "bank_commission_rub": int(bank_commission),
        "customs_rub": int(result["with_util"]),
        "extra_costs_rub": int(extra_costs),
        "age_bucket": age,
    }


