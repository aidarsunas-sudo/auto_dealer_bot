# -*- coding: utf-8 -*-
"""
test_price_formula.py — контрольные точки цены «под ключ в Казани»
(python test_price_formula.py, без pytest и без сети).

Курсы и параметры формулы ЗАФИКСИРОВАНЫ внутри теста (не живой ЦБ и не .env/
секреты Actions) — тест проверяет арифметику calculate_price_details(), а не
текущие настройки. Параметры (с 2026-10-07):
    корейская надбавка к цене по ЦБ  EXCHANGE_SPREAD_PCT   = 14%  (аукцион, экспортные
                                                                    документы, спред по воне)
    комиссия банка                    BANK_COMMISSION_PCT   = 0,5% (от цены с надбавкой)
    логистика + брокер + наценка      320 000 + 100 000 + 150 000 = 570 000 ₽
    ввоз физлицом на себя             IMPORTER_TYPE = phys

Лот: Kia Ray Signature, 998 см³, 76 л.с., бензин, выпуск 12.2022.
"""

import config
import post_builder

RATES = {"krw_rub": 0.0625, "eur_rub": 95.33, "usd_rub": 85.0, "cny_rub": 12.0, "jpy_rub": 0.55}
PARAMS = {"EXCHANGE_SPREAD_PCT": 0.14, "BANK_COMMISSION_PCT": 0.005, "LOGISTICS_RUB": 320000.0,
          "BROKER_FEE_RUB": 100000.0, "MARGIN_RUB": 150000.0, "IMPORTER_TYPE": "phys"}
KIA_RAY = {"cc": 998, "hp": 76, "fuel_code": "petrol", "year": 2022, "month": 12}


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _price(bid: int) -> dict:
    return post_builder.calculate_price_details(dict(KIA_RAY, price=bid), RATES)


def run() -> None:
    saved = {name: getattr(config, name) for name in PARAMS}
    for name, value in PARAMS.items():
        setattr(config, name, value)
    results = []
    try:
        p12, p13 = _price(12_000_000), _price(13_000_000)

        results.append(_check("12 млн ₩: цена машины по ЦБ 750 000 + надбавка 14% = 855 000",
                              p12["car_price_rub"] == 855_000 and p12["exchange_spread_rub"] == 105_000))
        results.append(_check("12 млн ₩: комиссия банка 0,5% от 855 000 = 4 275",
                              p12["bank_commission_rub"] == 4_275))
        results.append(_check("таможня Kia Ray (пошлина + сбор + льготный утильсбор 5 200) = 154 684 при евро 95,33",
                              p12["customs_rub"] == 154_684))
        results.append(_check("логистика + брокер + наценка = 570 000", p12["extra_costs_rub"] == 570_000))
        results.append(_check("12 млн ₩: итого под ключ 1 583 959",
                              p12["total_rub"] == 855_000 + 154_684 + 4_275 + 570_000 == 1_583_959))
        results.append(_check("13 млн ₩: итого под ключ 1 655 565",
                              p13["total_rub"] == 926_250 + 154_684 + 4_631 + 570_000 == 1_655_565))
        results.append(_check("каждый +1 млн ₩ ≈ +71,6 тыс. ₽",
                              70_000 < p13["total_rub"] - p12["total_rub"] < 73_000))
        results.append(_check("возрастная категория 3–5 лет", p12["age_bucket"] == "35"))

        config.BANK_COMMISSION_PCT = 0.025
        results.append(_check("при комиссии 2,5% итог выше на 1,6 тыс. ₽ (контроль, что параметр читается)",
                              _price(12_000_000)["total_rub"] > p12["total_rub"]))
    finally:
        for name, value in saved.items():
            setattr(config, name, value)

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
