# -*- coding: utf-8 -*-
"""
post_builder.py — здесь функция, которая превращает "сухие" данные о
машине (словарь с цифрами) в красивый читаемый текст для поста в Telegram.

Это ШАБЛОННЫЙ генератор (просто подставляет цифры в заготовленный текст).
Есть ещё post_generator.py — "умный" генератор через Claude API, который
использует ТЕ ЖЕ расчёты (см. calculate_price_details() ниже — общая
функция для обоих генераторов, чтобы цифры точно не разъезжались).
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
        car: словарь с полями price, cc, hp, fuel_code, year (см.
             scraper.parse_car_page() / used_car_scraper.parse_used_car_list()).
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

    if not all([price_krw, cc, hp, fuel_code]):
        return None

    # Возраст считаем по реальному году выпуска, если он известен (машина
    # с пробегом). Если года нет — это каталог НОВЫХ авто у дилера, и мы
    # точно знаем, что машина актуального модельного года.
    age = age_bucket_from_year(manufacture_year) if manufacture_year else age_bucket_for_new_catalog_car()

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


def build_telegram_post(car: dict, rates: dict) -> str:
    """
    Собирает текст поста из данных одной машины (шаблонный способ —
    без Claude API, просто подставляет цифры в заготовленные строки).

    Args:
        car: словарь с полями brand, model, trim, year, price, cc, hp,
             fuel_code, specs, url.
        rates: словарь курсов валют — нужен для расчёта таможни.

    Returns:
        Готовый текст поста, с эмодзи и переносами строк.
    """
    brand = car.get("brand", "")
    model = car.get("model", "")
    trim = car.get("trim", "")
    specs = car.get("specs", "")
    url = car.get("url", "")
    mileage_km = car.get("mileage_km")
    manufacture_year = car.get("year")
    price_krw = car.get("price")

    title_line = f"🚘 {brand} {model}"
    if trim:
        title_line += f" — {trim}"

    lines = [title_line, ""]

    if manufacture_year:
        lines.append(f"📅 Год выпуска: {manufacture_year}")
    if mileage_km is not None:
        mileage_formatted = f"{mileage_km:,}".replace(",", " ")
        lines.append(f"🛣 Пробег: {mileage_formatted} км")
    if specs:
        lines.append(f"🔧 {specs}")

    error_message = None
    details = None
    try:
        details = calculate_price_details(car, rates)
    except Exception as error:
        error_message = str(error)

    if error_message:
        # Расчёт сломался с ошибкой — показываем цену в Корее без
        # пересчёта, с честной пометкой, а не притворяемся, что всё ок.
        lines.append("")
        if price_krw:
            price_formatted = f"{price_krw:,}".replace(",", " ")
            lines.append(f"💰 Цена в Корее: {price_formatted} ₩")
        lines.append(f"⚠️ Не удалось точно посчитать таможню ({error_message})")
    elif details is None:
        # Не хватает исходных данных (объёма/мощности/топлива) — тоже
        # честно, без выдумывания цифр.
        lines.append("")
        if price_krw:
            price_formatted = f"{price_krw:,}".replace(",", " ")
            lines.append(f"💰 Цена в Корее: {price_formatted} ₩")
            lines.append("⚠️ Точная цена под ключ не посчитана (не хватает данных)")
        else:
            lines.append("💰 Цена уточняется")
    else:
        # Всё посчиталось нормально
        total_formatted = f"{details['total_rub']:,}".replace(",", " ")
        lines.append("")
        lines.append(f"💰 Цена под ключ Казань: {total_formatted} ₽")
        lines.append("✅ Включает пошлину, ЕТС, утильсбор" +
                      (", доставку и комиссию" if details["extra_costs_rub"] else ""))

    if url:
        lines.append("")
        lines.append(f"🔗 {url}")

    return "\n".join(lines)
