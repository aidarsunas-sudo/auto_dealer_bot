# -*- coding: utf-8 -*-
"""
Точный Python-порт логики из customs_calc.html (Pick+ Auto).
Источник истины по формулам — сам HTML-калькулятор; при изменении ставок
там же нужно поправить и здесь (или прислать новый customs_calc.html —
можно будет пересобрать этот файл заново).

ВСЕ ЧИСЛА (таблицы сбора, ЕТС, утильсбора, пороги льготы) лежат в customs_rates.py —
здесь только их применение. Ставки актуальны на: ПП №1291 (ред. №1713 от
01.11.2025), ПП №1638 от 23.10.2025.

Использование:
    from customs_calculator import calc_full

    result = calc_full(
        price=35_000_000, currency="krw",
        cc=1998, power=150, power_unit="hp",
        age="35",             # 'u3' | '35' | '57' | 'o7'
        fuel="diesel",        # 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec' | 'lpg'
        who="phys",            # 'phys' | 'legal'
        rates={"krw_rub": 0.050775, "eur_rub": 89.2743, "usd_rub": 78.2696,
               "cny_rub": 10.80, "jpy_rub": 54.00},
    )
    print(result["total_with_car"])
"""

from typing import Optional

from customs_rates import (
    ETS_ELEC_SHARE, ETS_PER_CC, ETS_UNDER_3Y_BANDS, FEE_ABOVE, FEE_BANDS,
    UTIL_BASE_RUB, UTIL_COMMERCIAL, UTIL_COMMERCIAL_TOP, UTIL_ELEC_30MIN_FACTOR,
    UTIL_ELEC_KW_PER_HP, UTIL_ELEC_LGOTA_COEFF, UTIL_ELEC_LGOTA_MAX_HP30,
    UTIL_ELEC_NEW, UTIL_ELEC_OLD, UTIL_LGOTA_COEFF, UTIL_LGOTA_MAX_CC, UTIL_LGOTA_MAX_HP,
)


# ═══════════════════════════════════════════════════════════
# ТАМОЖЕННЫЙ СБОР — ПП №1638 от 23.10.2025 (с 01.01.2026)
# Проверено: 1 777 125 руб → 13 541 руб
# ═══════════════════════════════════════════════════════════
def fee(rub: float) -> int:
    for limit, value in FEE_BANDS:
        if rub <= limit:
            return value
    return FEE_ABOVE


# ═══════════════════════════════════════════════════════════
# ЕТС — Решение Совета ЕЭК №107 от 20.12.2017
# Проверено: 1998 см³, 3-5 лет → 2.7 €/см³ = 5394.6 EUR → при
# eur_rub=88.03 (DEFAULT_RATES, обновлено 2026-07-06) = 474 886.64 руб.
# Формула не зависит от конкретного eur_rub — при обновлении DEFAULT_RATES
# поменяется и это число, самотест ниже пересчитывает его от актуальных
# DEFAULT_RATES, а не сверяет с зашитой константой.
#
# Электромобили (fuel="elec", добавлено 2026-07-17): у EV физически нет
# объёма двигателя — считать ЕТС по см³ для них бессмысленно (а раньше
# calc_full() всё равно передавал сюда произвольный/нулевой cc, что дало бы
# неверную сумму). Ставка для электромобилей в РФ — фиксированные 15% от
# стоимости авто, БЕЗ привязки к см³ и (в отличие от бензина/дизеля) БЕЗ
# деления по возрасту — действует с 01.01.2022.
# ⚠️ Источник — агрегаторы (moscowteslaclub.ru, auto.mail.ru), НЕ
# официальный текст решения ЕЭК/постановления и НЕ customs_calc.html
# (там EV-ветки нет вообще) — при появлении официального источника или
# обновлённого customs_calc.html сверить и поправить при необходимости.
# ═══════════════════════════════════════════════════════════
def ets_calc(cc: float, age: str, price_eur: float, eur_rub: float,
             fuel: str = "petrol", price_rub: float = 0) -> dict:
    if fuel == "elec":
        return {"rub": price_rub * ETS_ELEC_SHARE,
                "note": "электромобиль: 15% от стоимости (фиксированная ставка, без см³ и без деления по возрасту)"}

    if age == "u3":
        for lim, pct, min_r in ETS_UNDER_3Y_BANDS:
            if price_eur <= lim:
                return {
                    "rub": max(price_eur * pct, cc * min_r) * eur_rub,
                    "note": "до 3 лет: % или мин €/см³",
                }

    for lim, rate in ETS_PER_CC[age]:
        if cc <= lim:
            return {"rub": cc * rate * eur_rub, "note": f"{rate} €/см³ × {cc:.0f} см³"}

    raise ValueError(f"Не удалось посчитать ЕТС для cc={cc}, age={age}")


# ═══════════════════════════════════════════════════════════
# УТИЛИЗАЦИОННЫЙ СБОР — ПП №1291 (ред. №1713), с 01.01.2026
# ═══════════════════════════════════════════════════════════

# Порог льготного утильсбора для физлица (см. lgota ниже) — именованная
# константа (значение в customs_rates.UTIL_LGOTA_MAX_HP), потому что
# yandex_post_validator.py (own-lot витрина) сверяется с ЭТИМ ЖЕ порогом
# при проверке "для этой модели есть известная версия в пределах льготы"
# (см. model_specs.model_has_privileged_hp_variant()) — один источник
# правды на число, а не два синхронизируемых вручную.
MAX_HP_FOR_PHYS_LGOTA = UTIL_LGOTA_MAX_HP


def util_calc(cc: float, hp: float, age_util: str, fuel: str, who: str) -> dict:
    B = UTIL_BASE_RUB
    is_new = age_util == "u3"

    if fuel == "elec":
        kw30 = hp * UTIL_ELEC_30MIN_FACTOR * UTIL_ELEC_KW_PER_HP
        hp30 = round(hp * UTIL_ELEC_30MIN_FACTOR)
        lgota_elec = who == "phys" and hp30 <= UTIL_ELEC_LGOTA_MAX_HP30
        if lgota_elec:
            k = UTIL_ELEC_LGOTA_COEFF
            return {"rub": round(B * k), "coeff": k, "lgota": True,
                    "note30": f"30-мин: {hp30} л.с. ({hp} × 0.45)"}
        for lim, k in (UTIL_ELEC_NEW if is_new else UTIL_ELEC_OLD):
            if kw30 <= lim:
                return {"rub": round(B * k), "coeff": k, "lgota": False,
                        "note30": f"30-мин: {hp30} л.с. ({hp} × 0.45)"}

    lgota = who == "phys" and hp <= MAX_HP_FOR_PHYS_LGOTA and cc <= UTIL_LGOTA_MAX_CC
    if lgota:
        k = UTIL_LGOTA_COEFF["u3" if is_new else "o3"]
        return {"rub": round(B * k), "coeff": k, "lgota": True}

    top = UTIL_COMMERCIAL_TOP[0 if is_new else 1]
    if cc <= UTIL_LGOTA_MAX_CC:
        for lim, k0, k3 in UTIL_COMMERCIAL:
            if hp <= lim:
                k = k0 if is_new else k3
                return {"rub": round(B * k), "coeff": k, "lgota": False}
    else:
        return {"rub": round(B * top), "coeff": top, "lgota": False}

    return {"rub": round(B * top), "coeff": top, "lgota": False}


DEFAULT_RATES = {
    # Запасные курсы на случай сбоя rates.fetch_cbr_rates() — обновлены при
    # аудите 2026-09-18 по данным прогонов на GitHub Actions за 09-15..09-18
    # (см. gh run view --log): EUR=97.50, USD=84.51, KRW(за 1000)=61.76.
    # Предыдущие значения (от 2026-07-06): EUR=88.03, USD=77.23, KRW=49.69 —
    # отставали на 24% по KRW и 11% по EUR, что давало занижение цены авто
    # на ~240К₽ для типичного лота в 20М₩ при недоступности ЦБ.
    # cny_rub/jpy_rub не менялись (нет прямых источников для сверки в логах).
    "krw_rub": 0.06176,
    "cny_rub": 10.80,
    "jpy_rub": 54.00,
    "eur_rub": 97.50,
    "usd_rub": 84.51,
}


def calc_full(price: float, currency: str, cc: float, power: float,
              age: str, fuel: str = "petrol", who: str = "phys",
              power_unit: str = "hp", rates: Optional[dict] = None) -> dict:
    """
    Полный расчёт: сбор + ЕТС + утильсбор + итог с ценой авто.

    age: 'u3' (<3 лет) | '35' (3-5 лет) | '57' (5-7 лет) | 'o7' (>7 лет)
    fuel: 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec' | 'lpg'
    who: 'phys' | 'legal'

    'lpg' (газобаллонное оборудование, добавлено 2026-07-17): в отличие от
    'elec' — НЕ отдельная ветка расчёта. У LPG-версий (Hyundai Sonata LPi,
    Renault QM6/SM6 LPe и т.п.) есть реальный объём двигателя того же
    бензинового блока — ЕТС/утильсбор в РФ считаются по нему точно так же,
    как для 'petrol' (отдельной ставки для LPG в действующих формулах нет,
    fuel='lpg' здесь маркер для учёта/аудита, а не для другой формулы).
    """
    rates = {**DEFAULT_RATES, **(rates or {})}

    currency = currency.lower()
    if currency == "krw":
        price_rub = price * rates["krw_rub"]
    elif currency == "cny":
        price_rub = price * rates["cny_rub"]
    elif currency == "jpy":
        price_rub = price * rates["jpy_rub"] / 100
    elif currency == "eur":
        price_rub = price * rates["eur_rub"]
    elif currency == "usd":
        price_rub = price * rates["usd_rub"]
    else:
        price_rub = price
    price_eur = price_rub / rates["eur_rub"]

    hp = power if power_unit == "hp" else round(power * 1.35962)

    age_util = "u3" if age == "u3" else "o3"

    fee_rub = fee(price_rub)
    ets = ets_calc(cc, age, price_eur, rates["eur_rub"], fuel=fuel, price_rub=price_rub)
    util = util_calc(cc, hp, age_util, fuel, who)

    customs_total = fee_rub + ets["rub"]
    with_util = customs_total + util["rub"]

    return {
        "price_rub": price_rub,
        "fee_rub": fee_rub,
        "ets_rub": ets["rub"],
        "ets_note": ets["note"],
        "util_rub": util["rub"],
        "util_coeff": util["coeff"],
        "util_lgota": util["lgota"],
        "customs_total": customs_total,
        "with_util": with_util,
        "total_with_car": price_rub + with_util,
        "hp": hp,
    }


if __name__ == "__main__":
    # Контрольные примеры из комментариев исходного calc-html. Ожидаемое
    # значение ЕТС считается от ТЕКУЩИХ DEFAULT_RATES (не зашито константой)
    # — так тест не "ломается" визуально при каждом обновлении курса, а
    # проверяет именно формулу (1998см³ * 2.7 €/см³ * eur_rub).
    r1 = calc_full(price=35_000_000, currency="krw", cc=1998, power=150,
                    age="35", fuel="petrol", who="phys")
    expected_ets = round(1998 * 2.7 * DEFAULT_RATES["eur_rub"], 2)
    actual_ets = round(r1["ets_rub"], 2)
    match = "OK" if actual_ets == expected_ets else "MISMATCH"
    print(f"[{match}] Тест ЕТС: {actual_ets} (ожидаем {expected_ets}, по формуле 1998см³ × 2.7 €/см³ × eur_rub)")

    print("Тест сбора: fee(1777125) =", fee(1_777_125), "(ожидаем 13541)")
