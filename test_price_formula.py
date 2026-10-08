# -*- coding: utf-8 -*-
"""
test_price_formula.py — контрольные точки цены «под ключ в Казани» и предупреждения
pricing.calculate_price() (python test_price_formula.py, без pytest и без сети).

Курсы и параметры ЗАФИКСИРОВАНЫ внутри теста (не живой ЦБ и не .env/секреты Actions) —
тест проверяет арифметику формулы, а не текущие настройки. Параметры (с 2026-10-07):
    корейская надбавка к цене по ЦБ  KOREA_COSTS_PCT      = 14%  (аукцион, экспортные
                                                                   документы, спред по воне)
    банковский перевод               BANK_COMMISSION_PCT  = 0,5% (от цены с надбавкой)
    логистика + брокер + наценка     320 000 + 100 000 + 150 000 = 570 000 ₽
    ввоз физлицом на себя            IMPORTER_TYPE = phys

Лот: Kia Ray Signature, 998 см³, 76 л.с., бензин, выпуск 12.2022.
"""

from datetime import date, timedelta

import config
import post_builder
import pricing
from pricing import PricingParams, calculate_price

RATES = {"krw_rub": 0.0625, "eur_rub": 95.33, "usd_rub": 85.0, "cny_rub": 12.0, "jpy_rub": 0.55}
PARAMS = PricingParams(korea_costs_pct=0.14, bank_commission_pct=0.005, logistics_rub=320000.0,
                       broker_fee_rub=100000.0, margin_rub=150000.0, importer_type="phys")
KIA_RAY = {"cc": 998, "hp": 76, "fuel_code": "petrol", "year": 2022, "month": 12}


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def run() -> None:
    results = []
    ray = lambda bid, **kw: calculate_price(dict(KIA_RAY, price=bid, **kw), RATES, PARAMS)
    p12, p13 = ray(12_000_000), ray(13_000_000)

    # ── контрольные точки Kia Ray ─────────────────────────────────────────
    results.append(_check("12 млн ₩: машина по ЦБ 750 000 + надбавка 14% = 855 000",
                          p12.car_price_cbr_rub == 750_000 and p12.korea_costs_rub == 105_000 and p12.car_price_rub == 855_000))
    results.append(_check("12 млн ₩: банковский перевод 0,5% от 855 000 = 4 275", p12.bank_commission_rub == 4_275))
    results.append(_check("таможня Kia Ray = сбор 6 775 + ЕТС + льготный утильсбор 5 200 = 154 684 при евро 95,33",
                          p12.customs_rub == 154_684 and p12.fee_rub == 6_775 and p12.util_rub == 5_200
                          and p12.fee_rub + p12.duty_rub + p12.util_rub == p12.customs_rub))
    results.append(_check("логистика + брокер + наценка = 570 000", p12.extra_costs_rub == 570_000))
    results.append(_check("12 млн ₩: итого под ключ 1 583 959",
                          p12.total_rub == 855_000 + 154_684 + 4_275 + 570_000 == 1_583_959))
    results.append(_check("13 млн ₩: итого под ключ 1 655 565",
                          p13.total_rub == 926_250 + 154_684 + 4_631 + 570_000 == 1_655_565))
    results.append(_check("каждый +1 млн ₩ ≈ +71,6 тыс. ₽", 70_000 < p13.total_rub - p12.total_rub < 73_000))
    results.append(_check("возрастная категория 3–5 лет", p12.age_bucket == "35"))
    results.append(_check("Kia Ray (физлицо, 76 л.с., месяц известен): предупреждений нет", p12.warnings == []))

    # ── параметры работают, а не зашиты ──────────────────────────────────
    bank25 = calculate_price(dict(KIA_RAY, price=12_000_000), RATES, PricingParams(**{**PARAMS.__dict__, "bank_commission_pct": 0.025}))
    results.append(_check("комиссия 2,5% вместо 0,5% -> итог выше ровно на 17 100 ₽ (2% от 855 000)",
                          bank25.total_rub - p12.total_rub == 17_100))
    no_costs = calculate_price(dict(KIA_RAY, price=12_000_000), RATES, PricingParams(**{**PARAMS.__dict__, "korea_costs_pct": 0.0}))
    results.append(_check("надбавка 0% -> машина по ЦБ без надбавки", no_costs.car_price_rub == 750_000 and no_costs.korea_costs_rub == 0))

    # ── предупреждения про утильсбор ─────────────────────────────────────
    touareg = calculate_price({"price": 40_000_000, "cc": 2998, "hp": 286, "fuel_code": "diesel", "year": 2022, "month": 6}, RATES, PARAMS)
    results.append(_check("286 л.с.: коммерческий утильсбор 2 011 200 ₽, предупреждение называет мощность",
                          touareg.util_lgota is False and touareg.util_rub == 2_011_200
                          and any("КОММЕРЧЕСКИЙ" in w and "2 011 200" in w and "286" in w for w in touareg.warnings)))
    big_engine = calculate_price({"price": 30_000_000, "cc": 3500, "hp": 150, "fuel_code": "petrol", "year": 2022, "month": 6}, RATES, PARAMS)
    results.append(_check("3500 см³ при 150 л.с.: нет льготы, причина — объём",
                          big_engine.util_lgota is False and any("объём 3500" in w for w in big_engine.warnings)))
    legal = calculate_price(dict(KIA_RAY, price=12_000_000), RATES, PricingParams(**{**PARAMS.__dict__, "importer_type": "legal"}))
    results.append(_check("ввоз юрлицом: сбор коммерческий, причина — IMPORTER_TYPE, 5 200 ₽ молча не считаются",
                          legal.util_lgota is False and legal.util_rub > 5_200
                          and any("IMPORTER_TYPE" in w for w in legal.warnings)))
    edge = calculate_price(dict(KIA_RAY, price=12_000_000, hp=160), RATES, PARAMS)
    over = calculate_price(dict(KIA_RAY, price=12_000_000, hp=161), RATES, PARAMS)
    results.append(_check("граница льготы: 160 л.с. — льгота без предупреждений, 161 л.с. — предупреждение",
                          edge.util_lgota is True and edge.warnings == [] and over.util_lgota is False and len(over.warnings) == 1))
    ev = calculate_price({"price": 40_000_000, "cc": 1, "hp": 150, "fuel_code": "elec", "year": 2023, "month": 3}, RATES, PARAMS)
    results.append(_check("электромобиль: предупреждений про утильсбор нет (у него своя таблица)", ev is not None and ev.warnings == []))

    # ── прочие предупреждения ────────────────────────────────────────────
    fallback = calculate_price(dict(KIA_RAY, price=12_000_000), {**RATES, "_source": "fallback"}, PARAMS)
    results.append(_check("запасные курсы ЦБ -> предупреждение, расчёт тот же",
                          any("курсы ЦБ недоступны" in w for w in fallback.warnings) and fallback.total_rub == p12.total_rub))
    live = calculate_price(dict(KIA_RAY, price=12_000_000), {**RATES, "_source": "cbr"}, PARAMS)
    results.append(_check("свежие курсы (_source=cbr) -> без предупреждения", live.warnings == []))
    no_month = calculate_price({k: v for k, v in dict(KIA_RAY, price=12_000_000).items() if k != "month"}, RATES, PARAMS)
    results.append(_check("нет месяца выпуска -> предупреждение про возраст", any("месяц выпуска" in w for w in no_month.warnings)))
    results.append(_check("не хватает данных (нет мощности) -> None, не гадаем",
                          calculate_price({"price": 12_000_000, "cc": 998, "fuel_code": "petrol", "year": 2022}, RATES, PARAMS) is None))

    # ── КОНТРОЛЬНЫЙ ЛОТ Kia Ray Signature: фиксированный курс покупки 15,2 ₩/₽ ──────────────
    # Допуск 1 ₽ (округление курса евро: опубликованное число посчитано при евро ≈ 95,3349).
    ctrl_rates = {**RATES, "krw_rub": 0.06247, "eur_rub": 95.3349}
    ctrl = PricingParams(**{**PARAMS.__dict__, "krw_per_rub": 15.2, "krw_rate_updated": date.today().isoformat()})
    expected = {0.025: {12: 1_533_901, 13: 1_601_335}, 0.005: {12: 1_518_112, 13: 1_584_230}}
    for bank, per_bid in expected.items():
        for mln, wanted in per_bid.items():
            got = calculate_price(dict(KIA_RAY, price=mln * 1_000_000), ctrl_rates,
                                  PricingParams(**{**ctrl.__dict__, "bank_commission_pct": bank})).total_rub
            print(f"     контрольный лот: банк {bank:.1%}, ставка {mln} млн ₩ -> формула {got:,} ₽, ожидается {wanted:,} ₽ (разница {got - wanted:+d})".replace(",", " "))
            results.append(_check(f"контрольный лот Kia Ray Signature: банк {bank:.1%}, ставка {mln} млн ₩ = {wanted:,} ₽ (допуск 1 ₽)".replace(",", " "),
                                  abs(got - wanted) <= 1))
    fixed = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, ctrl)
    results.append(_check("режим фиксированного курса: машина = 12 000 000 / 15,2 = 789 473 ₽, надбавка к ЦБ ≈ +5,3% (а не 14%)",
                          fixed.car_price_rub == 789_473 and 0.05 < fixed.korea_costs_rub / fixed.car_price_cbr_rub < 0.055))
    results.append(_check("при KOREA_COSTS_PCT режим фиксированного курса его не применяет (цена не зависит от 14%)",
                          calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**ctrl.__dict__, "korea_costs_pct": 0.50})).total_rub
                          == fixed.total_rub))
    default_mode = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**PARAMS.__dict__, "bank_commission_pct": 0.025}))
    fixed_25 = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**ctrl.__dict__, "bank_commission_pct": 0.025}))
    results.append(_check("при одинаковой комиссии 2,5% режим «ЦБ + 14%» (по умолчанию) даёт цену на ~67 тыс. ₽ выше фиксированного курса 15,2",
                          60_000 < default_mode.total_rub - fixed_25.total_rub < 75_000))
    stale = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**ctrl.__dict__, "krw_per_rub": 17.5}))
    wild = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**ctrl.__dict__, "krw_per_rub": 10.0}))
    results.append(_check("курс покупки выгоднее ЦБ (надбавка < 0) или завышен > 25% -> предупреждение проверить KRW_PER_RUB",
                          any("KRW_PER_RUB" in w for w in stale.warnings) and any("KRW_PER_RUB" in w for w in wild.warnings)
                          and not any("KRW_PER_RUB" in w for w in fixed.warnings)))
    # ── возраст курса покупки: дата обновления и порог 7 дней ───────────────
    today = date(2026, 10, 15)
    def with_date(updated):
        return calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates,
                               PricingParams(**{**ctrl.__dict__, "krw_rate_updated": updated}), today=today)
    has_rate_warning = lambda r: any("обновлялся" in w or "KRW_PER_RUB_UPDATED" in w for w in r.warnings)
    results.append(_check("курс обновлён 7 дней назад (порог не превышен) -> предупреждения нет", not has_rate_warning(with_date("2026-10-08"))))
    results.append(_check("курс старше 7 дней (8 дн.) -> предупреждение с датой и дней назад", any("8 дн." in w and "2026-10-07" in w for w in with_date("2026-10-07").warnings)))
    results.append(_check("дата курса не задана -> предупреждение", has_rate_warning(with_date(None))))
    results.append(_check("дата в неверном формате -> предупреждение, расчёт не падает", with_date("вчера").total_rub > 0 and has_rate_warning(with_date("вчера"))))
    no_fixed = calculate_price(dict(KIA_RAY, price=12_000_000), ctrl_rates, PricingParams(**{**PARAMS.__dict__, "krw_rate_updated": "2020-01-01"}), today=today)
    results.append(_check("режим «ЦБ + надбавка» (курс не задан) о возрасте курса не предупреждает", not has_rate_warning(no_fixed)))
    results.append(_check("значения в config по умолчанию: курс 15,2, дата обновления задана, порог 7 дней, расходы 320+100+150 тыс. ₽",
                          config.KRW_PER_RUB in (15.2, None) and bool(config.KRW_PER_RUB_UPDATED) and config.KRW_RATE_MAX_AGE_DAYS == 7))
    saved_rate = config.KRW_PER_RUB
    try:
        config.KRW_PER_RUB = 15.2
        results.append(_check("PricingParams.from_config() читает config.KRW_PER_RUB", PricingParams.from_config().krw_per_rub == 15.2))
        config.KRW_PER_RUB = None
        results.append(_check("KRW_PER_RUB не задан -> None (режим «ЦБ + надбавка»)", PricingParams.from_config().krw_per_rub is None))
    finally:
        config.KRW_PER_RUB = saved_rate

    # ── обратная совместимость: post_builder и config ─────────────────────
    saved = {n: getattr(config, n) for n in ("KOREA_COSTS_PCT", "BANK_COMMISSION_PCT", "LOGISTICS_RUB",
                                              "BROKER_FEE_RUB", "MARGIN_RUB", "IMPORTER_TYPE", "KRW_PER_RUB")}
    try:
        config.KRW_PER_RUB = None  # проверка прежнего режима «ЦБ + 14%»
        config.KOREA_COSTS_PCT, config.BANK_COMMISSION_PCT = 0.14, 0.005
        config.LOGISTICS_RUB, config.BROKER_FEE_RUB, config.MARGIN_RUB, config.IMPORTER_TYPE = 320000.0, 100000.0, 150000.0, "phys"
        legacy = post_builder.calculate_price_details(dict(KIA_RAY, price=12_000_000), RATES)
        results.append(_check("calculate_price_details(): тот же итог, старые ключи на месте, exchange_spread_rub == korea_costs_rub",
                              legacy["total_rub"] == 1_583_959 and legacy["exchange_spread_rub"] == legacy["korea_costs_rub"] == 105_000
                              and all(k in legacy for k in ("car_price_rub", "bank_commission_rub", "customs_rub", "extra_costs_rub", "age_bucket"))))
        results.append(_check("PricingParams.from_config() читает config на момент вызова",
                              PricingParams.from_config().korea_costs_pct == 0.14 and PricingParams.from_config().extra_costs_rub == 570_000))
        results.append(_check("calculate_price_details() без цены -> None",
                              post_builder.calculate_price_details({"cc": 998, "hp": 76, "fuel_code": "petrol"}, RATES) is None))
    finally:
        for name, value in saved.items():
            setattr(config, name, value)
    results.append(_check("устаревшее имя config.EXCHANGE_SPREAD_PCT совпадает с KOREA_COSTS_PCT при импорте",
                          config.EXCHANGE_SPREAD_PCT == config.KOREA_COSTS_PCT))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
