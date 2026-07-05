# -*- coding: utf-8 -*-
"""
Точный Python-порт логики из customs_calc.html (Pick+ Auto).
Источник истины по формулам — сам HTML-калькулятор; при изменении ставок
там же нужно поправить и здесь (или прислать новый customs_calc.html —
можно будет пересобрать этот файл заново).

Ставки актуальны на: ПП №1291 (ред. №1713 от 01.11.2025), ПП №1638 от 23.10.2025

Использование:
    from customs_calculator import calc_full

    result = calc_full(
        price=35_000_000, currency="krw",
        cc=1998, power=150, power_unit="hp",
        age="35",             # 'u3' | '35' | '57' | 'o7'
        fuel="diesel",        # 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec'
        who="phys",            # 'phys' | 'legal'
        rates={"krw_rub": 0.050775, "eur_rub": 89.2743, "usd_rub": 78.2696,
               "cny_rub": 10.80, "jpy_rub": 54.00},
    )
    print(result["total_with_car"])
"""

from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════
# ТАМОЖЕННЫЙ СБОР — ПП №1638 от 23.10.2025 (с 01.01.2026)
# Проверено: 1 777 125 руб → 13 541 руб
# ═══════════════════════════════════════════════════════════
def fee(rub: float) -> int:
    if rub <= 200_000:   return 1693
    if rub <= 450_000:   return 3387
    if rub <= 1_200_000: return 6775
    if rub <= 2_700_000: return 13541
    if rub <= 4_200_000: return 19044
    if rub <= 5_500_000: return 24598
    if rub <= 7_000_000: return 31740
    if rub <= 8_000_000: return 36501
    if rub <= 9_000_000: return 39675
    if rub <= 10_000_000: return 49240
    return 73860


# ═══════════════════════════════════════════════════════════
# ЕТС — Решение Совета ЕЭК №107 от 20.12.2017
# Проверено: 1998 см³, 3-5 лет → 2.7 €/см³ → 481 599.14 руб
# ═══════════════════════════════════════════════════════════
def ets_calc(cc: float, age: str, price_eur: float, eur_rub: float) -> dict:
    if age == "u3":
        bands = [
            (8500,    0.54, 2.5),
            (16700,   0.48, 3.5),
            (42300,   0.48, 5.5),
            (84500,   0.48, 7.5),
            (169000,  0.48, 15),
            (float("inf"), 0.48, 20),
        ]
        for lim, pct, min_r in bands:
            if price_eur <= lim:
                return {
                    "rub": max(price_eur * pct, cc * min_r) * eur_rub,
                    "note": "до 3 лет: % или мин €/см³",
                }

    tbl = {
        "35": [(1000, 1.5), (1500, 1.7), (1800, 2.5), (2300, 2.7), (3000, 3.0), (float("inf"), 3.6)],
        "57": [(1000, 3.0), (1500, 3.2), (1800, 3.5), (2300, 4.8), (3000, 5.0), (float("inf"), 5.7)],
        "o7": [(1000, 3.0), (1500, 3.2), (1800, 3.5), (2300, 4.8), (3000, 5.0), (float("inf"), 5.7)],
    }
    for lim, rate in tbl[age]:
        if cc <= lim:
            return {"rub": cc * rate * eur_rub, "note": f"{rate} €/см³ × {cc:.0f} см³"}

    raise ValueError(f"Не удалось посчитать ЕТС для cc={cc}, age={age}")


# ═══════════════════════════════════════════════════════════
# УТИЛИЗАЦИОННЫЙ СБОР — ПП №1291 (ред. №1713), с 01.01.2026
# ═══════════════════════════════════════════════════════════
def util_calc(cc: float, hp: float, age_util: str, fuel: str, who: str) -> dict:
    B = 20000
    is_new = age_util == "u3"

    if fuel == "elec":
        kw30 = hp * 0.45 * 0.7355
        hp30 = round(hp * 0.45)
        lgota_elec = who == "phys" and hp30 <= 80
        if lgota_elec:
            k = 0.17
            return {"rub": round(B * k), "coeff": k, "lgota": True,
                    "note30": f"30-мин: {hp30} л.с. ({hp} × 0.45)"}
        t = [
            (58.85, 33.37), (73.55, 33.37), (95.61, 40.04), (117.68, 45.00),
            (139.75, 47.64), (161.81, 50.52), (183.88, 57.12), (205.94, 64.56),
            (float("inf"), 72.96),
        ] if is_new else [
            (58.85, 58.70), (73.55, 58.70), (95.61, 70.44), (117.68, 74.64),
            (139.75, 79.20), (161.81, 83.88), (183.88, 91.92), (205.94, 100.56),
            (float("inf"), 110.16),
        ]
        for lim, k in t:
            if kw30 <= lim:
                return {"rub": round(B * k), "coeff": k, "lgota": False,
                        "note30": f"30-мин: {hp30} л.с. ({hp} × 0.45)"}

    lgota = who == "phys" and hp <= 160 and cc <= 3000
    if lgota:
        k = 0.17 if is_new else 0.26
        return {"rub": round(B * k), "coeff": k, "lgota": True}

    comm = [
        (160, 40.04, 70.44), (190, 45.00, 74.64), (220, 47.64, 79.20),
        (250, 50.52, 83.88), (280, 57.12, 91.92), (310, 64.56, 100.56),
        (340, 72.96, 110.16), (370, 83.16, 120.60), (400, 94.80, 132.00),
        (430, 108.00, 144.60), (460, 123.24, 158.40), (500, 140.40, 173.40),
        (9999, 160.08, 189.84),
    ]

    if cc <= 3000:
        for lim, k0, k3 in comm:
            if hp <= lim:
                k = k0 if is_new else k3
                return {"rub": round(B * k), "coeff": k, "lgota": False}
    else:
        k = 160.08 if is_new else 189.84
        return {"rub": round(B * k), "coeff": k, "lgota": False}

    k = 160.08 if is_new else 189.84
    return {"rub": round(B * k), "coeff": k, "lgota": False}


DEFAULT_RATES = {
    "krw_rub": 0.050775,
    "cny_rub": 10.80,
    "jpy_rub": 54.00,
    "eur_rub": 89.2743,
    "usd_rub": 78.2696,
}


def calc_full(price: float, currency: str, cc: float, power: float,
              age: str, fuel: str = "petrol", who: str = "phys",
              power_unit: str = "hp", rates: Optional[dict] = None) -> dict:
    """
    Полный расчёт: сбор + ЕТС + утильсбор + итог с ценой авто.

    age: 'u3' (<3 лет) | '35' (3-5 лет) | '57' (5-7 лет) | 'o7' (>7 лет)
    fuel: 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec'
    who: 'phys' | 'legal'
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
    ets = ets_calc(cc, age, price_eur, rates["eur_rub"])
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
    # Контрольные примеры из комментариев исходного calc-html
    r1 = calc_full(price=35_000_000, currency="krw", cc=1998, power=150,
                    age="35", fuel="petrol", who="phys")
    print("Тест ЕТС (ожидаем ~481 599.14):", round(r1["ets_rub"], 2))

    print("Тест сбора: fee(1777125) =", fee(1_777_125), "(ожидаем 13541)")
