# -*- coding: utf-8 -*-
"""
pricing.py — цена «под ключ в Казани»: ОДНА функция расчёта с параметрами в
конфигурации и со списком предупреждений. Всё, что показывается в постах,
цифры в видео и отчётах считается здесь (post_builder.calculate_price_details —
тонкая обёртка для старых вызовов).

Формула (рубли):
    машина по ЦБ        = цена в ₩ × курс ЦБ (₩→₽)
    корейская надбавка  = машина по ЦБ × KOREA_COSTS_PCT        (14%: аукцион, экспортные
                                                                 документы, спред по воне)
    цена машины         = машина по ЦБ + корейская надбавка
    банковский перевод  = цена машины × BANK_COMMISSION_PCT     (0,5%)
    таможня             = сбор + ЕТС + утильсбор                (по ЦБ, customs_rates.py)
    логистика и прочее  = LOGISTICS_RUB + BROKER_FEE_RUB + MARGIN_RUB
    итого под ключ      = цена машины + банковский перевод + таможня + логистика и прочее

Где что хранится:
    параметры     config.py (KOREA_COSTS_PCT, BANK_COMMISSION_PCT, LOGISTICS_RUB,
                  BROKER_FEE_RUB, MARGIN_RUB, IMPORTER_TYPE) -> PricingParams
    курсы         rates.fetch_cbr_rates() (ЦБ; при сбое — запасные, с пометкой _source)
    ставки пошлин customs_rates.py (таблицы сбора, ЕТС по объёму двигателя, утильсбор)

ПРЕДУПРЕЖДЕНИЯ (PriceResult.warnings) — функция не молчит, когда расчёт верен
только при определённых условиях:
    - утильсбор НЕ льготный (5 200 ₽ неприменимы): мощность выше 160 л.с., объём выше
      3000 см³ или ввоз юрлицом — указана причина и сумма коммерческого сбора;
    - льготный утильсбор верен только при ввозе физлицом на себя для личного пользования;
      если IMPORTER_TYPE не phys — предупреждение;
    - курсы ЦБ не загрузились и взяты запасные;
    - возраст определён по году без месяца (консервативная категория).
Предупреждения не блокируют публикацию сами по себе; вызывающий код логирует их и
может показать оператору (publish_lot).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import config
from age_calculator import age_bucket_for_new_catalog_car, age_bucket_from_year
from customs_calculator import calc_full
from customs_rates import UTIL_LGOTA_MAX_CC, UTIL_LGOTA_MAX_HP

logger = logging.getLogger("pricing")


@dataclass(frozen=True)
class PricingParams:
    """Параметры формулы. Берутся из config на момент вызова (from_config), чтобы тесты и
    ручные расчёты могли подставлять свои значения без правки глобального состояния."""
    korea_costs_pct: float
    bank_commission_pct: float
    logistics_rub: float
    broker_fee_rub: float
    margin_rub: float
    importer_type: str

    @classmethod
    def from_config(cls) -> "PricingParams":
        return cls(
            korea_costs_pct=config.KOREA_COSTS_PCT,
            bank_commission_pct=config.BANK_COMMISSION_PCT,
            logistics_rub=config.LOGISTICS_RUB,
            broker_fee_rub=config.BROKER_FEE_RUB,
            margin_rub=config.MARGIN_RUB,
            importer_type=config.IMPORTER_TYPE,
        )

    @property
    def extra_costs_rub(self) -> float:
        return self.logistics_rub + self.broker_fee_rub + self.margin_rub


@dataclass
class PriceResult:
    total_rub: int
    car_price_rub: int            # цена машины с корейской надбавкой
    car_price_cbr_rub: int        # машина по курсу ЦБ (без надбавки)
    korea_costs_rub: int
    bank_commission_rub: int
    customs_rub: int              # сбор + ЕТС + утильсбор
    fee_rub: int
    duty_rub: int                 # ЕТС (пошлина)
    util_rub: int
    util_lgota: bool
    util_coeff: float
    extra_costs_rub: int
    age_bucket: str
    warnings: list = field(default_factory=list)

    def as_legacy_dict(self) -> dict:
        """Словарь в прежнем формате calculate_price_details() + новые ключи."""
        return {
            "total_rub": self.total_rub,
            "car_price_rub": self.car_price_rub,
            "bank_commission_rub": self.bank_commission_rub,
            "customs_rub": self.customs_rub,
            "extra_costs_rub": self.extra_costs_rub,
            "exchange_spread_rub": self.korea_costs_rub,   # старое имя ключа
            "korea_costs_rub": self.korea_costs_rub,
            "car_price_cbr_rub": self.car_price_cbr_rub,
            "fee_rub": self.fee_rub,
            "duty_rub": self.duty_rub,
            "util_rub": self.util_rub,
            "util_lgota": self.util_lgota,
            "util_coeff": self.util_coeff,
            "age_bucket": self.age_bucket,
            "warnings": list(self.warnings),
        }


def _util_warnings(util_lgota: bool, util_rub: int, util_coeff: float, hp: float, cc: float,
                   fuel: str, importer_type: str) -> list:
    """Предупреждения про утильсбор: льготные 5 200 ₽ верны только для физлица на себя."""
    if fuel == "elec":
        return []
    reasons = []
    if importer_type != "phys":
        reasons.append("ввоз не физлицом (IMPORTER_TYPE != phys)")
    if hp > UTIL_LGOTA_MAX_HP:
        reasons.append(f"мощность {hp:g} л.с. выше {UTIL_LGOTA_MAX_HP}")
    if cc > UTIL_LGOTA_MAX_CC:
        reasons.append(f"объём {cc:g} см³ выше {UTIL_LGOTA_MAX_CC}")
    if not util_lgota:
        return [f"утильсбор КОММЕРЧЕСКИЙ: {util_rub:,} ₽ (коэффициент {util_coeff}) — льготные 5 200 ₽ "
                f"неприменимы: {'; '.join(reasons) or 'условия льготы не выполнены'}".replace(",", " ")]
    if importer_type != "phys":  # на случай, если расчёт льготы когда-то разойдётся с IMPORTER_TYPE
        return [f"льготный утильсбор {util_rub:,} ₽ верен только при ввозе физлицом для личного пользования".replace(",", " ")]
    return []


def calculate_price(car: dict, rates: dict, params: Optional[PricingParams] = None) -> Optional[PriceResult]:
    """
    Цена под ключ в Казани.

    car: price (₩), cc, hp, fuel_code, year, month (month необязателен).
    rates: курсы (rates.fetch_cbr_rates()); ключ "_source" == "fallback" означает запасные.
    params: параметры формулы; по умолчанию из config.

    None — не хватает данных (нет цены/объёма/мощности/топлива): не гадаем.
    """
    params = params or PricingParams.from_config()
    price_krw, cc, hp, fuel = car.get("price"), car.get("cc"), car.get("hp"), car.get("fuel_code")
    if not all([price_krw, cc, hp, fuel]):
        return None

    year, month = car.get("year"), car.get("month")
    # Возраст — по реальному году выпуска, если известен; нет года — каталог новых авто.
    age = age_bucket_from_year(year, month) if year else age_bucket_for_new_catalog_car()

    customs = calc_full(price=price_krw, currency="krw", cc=cc, power=hp, age=age, fuel=fuel,
                        who=params.importer_type, rates=rates)

    cbr_price = customs["price_rub"]
    korea_costs = cbr_price * params.korea_costs_pct
    car_price = cbr_price + korea_costs
    bank_commission = car_price * params.bank_commission_pct
    extra = params.extra_costs_rub
    total = int(car_price + customs["with_util"] + bank_commission + extra)

    warnings = _util_warnings(customs["util_lgota"], customs["util_rub"], customs["util_coeff"],
                              customs["hp"], cc, fuel, params.importer_type)
    if (rates or {}).get("_source") == "fallback":
        warnings.append(f"курсы ЦБ недоступны — использованы запасные (₩ {rates.get('krw_rub')}, € {rates.get('eur_rub')}): "
                        f"цена приблизительная")
    if year and not month:
        warnings.append("месяц выпуска неизвестен — возрастная категория для ЕТС определена по году (консервативно)")

    return PriceResult(
        total_rub=total, car_price_rub=int(car_price), car_price_cbr_rub=int(cbr_price),
        korea_costs_rub=int(korea_costs), bank_commission_rub=int(bank_commission),
        customs_rub=int(customs["with_util"]), fee_rub=int(customs["fee_rub"]),
        duty_rub=int(customs["ets_rub"]), util_rub=int(customs["util_rub"]),
        util_lgota=bool(customs["util_lgota"]), util_coeff=customs["util_coeff"],
        extra_costs_rub=int(extra), age_bucket=age, warnings=warnings,
    )
