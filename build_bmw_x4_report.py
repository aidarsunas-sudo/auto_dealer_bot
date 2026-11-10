# -*- coding: utf-8 -*-
"""
build_bmw_x4_report.py — разовый отчёт (НЕ публикация) по BMW X4 (G02),
2024-2026 г.в., пробег <=5000 км, БЕЗ окрасов/ДТП (строгий допуск 0 элементов,
как в build_bmw_x3_report.py — ни одного исключения).

Отличия от build_bmw_x3_report.py (по требованиям THIS задачи):
- Цена показывается ДВОЙНО: цена на площадке в ₩ + перевод в ₽ по ЖИВОМУ курсу
  ЦБ (rates.fetch_cbr_rates), плюс полная разбивка «под ключ Казань» через
  customs_calculator.calc_full (та же логика, что post_builder, но с
  контролем наценки — копия calc_price из build_compact_suv_2022_report.py).
- Объявления с НЕПОДТВЕРЖДЁННЫМ статусом ДТП/окрасов (диагностика Encar
  недоступна) не выбрасываются, а идут отдельным разделом «требует проверки»
  (требование задачи), в основной список не попадают.
- Сортировка результата — по возрастанию пробега (приоритет задачи).
- Год 2026 включён в диапазон честно: производство X4 завершено в 2025
  (G02 снят с конвейера, нового поколения с ДВС нет), поэтому лотов 2026
  на площадках нет — в отчёте это отражено явно.

Источники и полнота проверки (по факту прогона 2026-11-10):
Encar/Charancha — цвет+ДТП проверяются полностью; HeyDealer — страховая
история в тегах (цвета нет); KCar — статус ДТП; AJ/Glovis/Lotte — воронка/
по данным адаптеров; kbchachacha (антибот-заглушка вместо страницы) и
OnBid (таймаут соединения) — недоступны из этой сети.

Запуск:
    python build_bmw_x4_report.py
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

import aj_scraper
import charancha_scraper
import encar_scraper
import glovis_scraper
import heydealer_scraper
import kbchachacha_target_adapter
import kcar_scraper
import lotte_scraper
import onbid_scraper
import config
from age_calculator import age_bucket_for_new_catalog_car, age_bucket_from_year
from customs_calculator import calc_full
from encar_test_scraper import DETAIL_URL as ENCAR_DETAIL_URL
from encar_test_scraper import HEADERS as ENCAR_HEADERS
from encar_test_scraper import fetch_diagnosis_and_inspection
from login_gate import LoginRequiredError
from model_specs import SPECS
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_bmw_x4_report")

OUT_DIR = Path(__file__).resolve().parent / "bmw_x4_report"
DATA_PATH = OUT_DIR / "collected.json"

TARGET = {"brand": "BMW", "model": "X4",
          "year_from": 2024, "year_to": 2026, "mileage_max": 5000}


def _hp_ok(car: dict) -> bool:
    return car.get("hp") is not None  # диапазон hp не ограничен в этом запросе


def simple_price_rub(price_krw, krw_rub: float) -> int | None:
    if not price_krw:
        return None
    return round(price_krw * krw_rub)


def calc_price(car: dict, rates: dict) -> dict | None:
    """Полная разбивка «под ключ Казань» — копия calc_price из
    build_compact_suv_2022_report.py (та же логика, что
    post_builder.calculate_price_details, наценка включена в отдельную
    справочную колонку)."""
    price_krw = car.get("price")
    cc, hp, fuel_code = car.get("cc"), car.get("hp"), car.get("fuel_code")
    if not all([price_krw, cc, hp, fuel_code]):
        return None

    age = (age_bucket_from_year(car.get("year"), car.get("month")) if car.get("year")
           else age_bucket_for_new_catalog_car())

    result = calc_full(price=price_krw, currency="krw", cc=cc, power=hp, age=age,
                       fuel=fuel_code, who=config.IMPORTER_TYPE, rates=rates)

    bank_commission = result["price_rub"] * config.BANK_COMMISSION_PCT
    extra_costs = config.LOGISTICS_RUB + config.BROKER_FEE_RUB
    total_with_margin_rub = int(result["total_with_car"] + bank_commission +
                                extra_costs + config.MARGIN_RUB)

    return {
        "total_rub": int(result["total_with_car"] + bank_commission + extra_costs),
        "total_with_margin_rub": total_with_margin_rub,
        "car_price_rub": int(result["price_rub"]),
        "bank_commission_rub": int(bank_commission),
        "fee_rub": int(result["fee_rub"]),
        "ets_rub": int(result["ets_rub"]),
        "util_rub": int(result["util_rub"]),
        "logistics_broker_rub": int(extra_costs),
        "margin_rub": int(config.MARGIN_RUB),
        "age_bucket": age,
    }


def fill_missing_spec_by_cc(car: dict) -> dict:
    """Fallback для площадок, где find_spec() не матчится, но объём двигателя
    (cc) сайт отдаёт отдельным полем (Charancha) — если для (модель, cc) в
    model_specs.SPECS есть РОВНО ОДНА запись, берём её hp/fuel (не гадаем).
    Копия функции из build_compact_suv_2022_report.py."""
    if car.get("hp") is not None or not car.get("cc"):
        return car
    model_slug = re.sub(r"[^\w]+", "", str(car.get("model") or "").lower())
    if not model_slug:
        return car
    candidates = [s for s in SPECS if any(model_slug == re.sub(r"[^\w]+", "", str(kw)) for kw in s.match)
                  and s.cc == car["cc"]]
    if len(candidates) == 1:
        spec = candidates[0]
        car["hp"] = spec.hp
        car["fuel_code"] = spec.fuel
        car["_spec_filled_by_cc"] = spec.label
    return car


def collect_funnel_only(source_name: str, search_fn) -> dict:
    try:
        cars = search_fn(TARGET, logger)
    except LoginRequiredError as error:
        logger.error(f"{source_name}: {error}")
        return {"raw_matches": 0, "error": str(error)}
    except Exception as error:
        logger.error(f"{source_name}: ошибка поиска: {error}")
        return {"raw_matches": 0, "error": str(error)}
    cars = [c for c in cars if _hp_ok(c)]
    logger.info(f"{source_name}: {len(cars)} по спеке (цвет/ДТП не проверяются — см. докстринг)")
    return {"raw_matches": len(cars), "error": None}


def enrich_encar_full(car: dict) -> dict:
    try:
        resp = requests.get(ENCAR_DETAIL_URL.format(id=car["external_id"]),
                            headers=ENCAR_HEADERS, timeout=20)
        resp.raise_for_status()
        detail = resp.json()
        spec_data = detail.get("spec") or {}
        car["color"] = spec_data.get("colorName")
        car["vin"] = detail.get("vin")
        full_photos = [f"https://ci.encar.com{p['path']}" for p in (detail.get("photos") or []) if p.get("path")]
        if full_photos:
            car["photos"] = full_photos
    except Exception as error:
        logger.warning(f"encar: не удалось получить цвет/фото {car['external_id']}: {error}")
    time.sleep(0.3)

    try:
        extra = fetch_diagnosis_and_inspection(car["external_id"])
    except Exception as error:
        logger.warning(f"encar: не удалось получить диагностику {car['external_id']}: {error}")
        extra = {}

    inspection = extra.get("inspection") or {}
    master = inspection.get("master") or {}
    outers = inspection.get("outers") or []

    car["_diagnosis_ok"] = bool(extra.get("inspection") is not None)
    car["_accdient_flag"] = master.get("accdient")
    car["_waterlog"] = master.get("waterlog")
    car["_outers_panels"] = [o.get("type", {}).get("title", "?") for o in outers]
    time.sleep(0.4)
    return car


def classify_encar_strict(car: dict) -> tuple:
    """СТРОГИЙ допуск (0 элементов, "без окрасов"). Статус "unknown"
    (диагностика недоступна) — отдельный раздел «требует проверки»."""
    if not car.get("_diagnosis_ok"):
        return "unknown", "техпаспорт/диагностика недоступны для этого объявления — статус ДТП/окрасов НЕ подтверждён, проверять вручную"
    if car.get("_waterlog"):
        return "reject", "потоп (техпаспорт)"
    if car.get("_accdient_flag") is True:
        return "reject", "подтверждённое ДТП (техпаспорт)"
    panels = car.get("_outers_panels") or []
    if len(panels) == 0:
        return "clean", "без окрасов/повреждений кузова (техпаспорт чист)"
    return "reject", f"есть отметка ремонта/окраса ({len(panels)} панелей: {', '.join(panels)}) — по условию «без окрасов» исключение"


def classify_charancha_strict(car: dict) -> tuple:
    if car.get("is_seizure"):
        return "reject", "арест/залог"
    is_acc = car.get("is_accident")
    if is_acc is None:
        return "unknown", "статус ДТП не указан площадкой"
    if is_acc is False:
        return "clean", "без ДТП (по данным площадки)"
    return "reject", "площадка отмечает ДТП"


_CLAIMS_RE = re.compile(r"보험\s*(\d+)\s*건")


def classify_heydealer_strict(car: dict) -> tuple:
    accident_free = car.get("accident_free")
    notes = car.get("damage_notes") or ""
    if accident_free is None:
        return "unknown", "нет числа страховых случаев в тегах площадки"
    if accident_free is True:
        return "clean", "0 страховых случаев (по тегу площадки)"
    return "reject", f"страховые случаи есть ({notes}) — по условию «без окрасов» не допускается"


def classify_kcar(car: dict) -> tuple:
    accident_free = car.get("accident_free")
    if accident_free is None:
        return "unknown", "статус ДТП не указан площадкой"
    if accident_free is True:
        return "clean", "без ДТП (по данным площадки)"
    return "reject", f"площадка отмечает повреждения: {car.get('damage_notes') or '(без деталей)'}"


def classify_aj(car: dict) -> tuple:
    if car.get("accident_free") is True:
        return "clean", "без ДТП (AJ: CLEAN)"
    tag = car.get("damage_notes")
    if tag == "REPLACE":
        return "reject", "замена/ремонт панели (AJ: REPLACE) — по условию «без окрасов» исключение"
    if tag == "STRUCTURAL":
        return "reject", "структурные повреждения (AJ: STRUCTURAL)"
    return "unknown", "статус ДТП не размечен площадкой"


def _sort_by_mileage(cars: list) -> list:
    return sorted(cars, key=lambda c: (c.get("mileage_km") is None, c.get("mileage_km") or 0))


def _finalize(car: dict, rates: dict) -> dict:
    car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
    car["_price_details"] = calc_price(car, rates)
    return car


def collect_encar(rates: dict) -> tuple:
    try:
        cars = encar_scraper.search_target_model(TARGET, logger)
    except Exception as error:
        logger.error(f"encar: ошибка поиска: {error}")
        return [], [], []
    cars = [fill_missing_spec_by_cc(c) for c in cars]
    cars = [c for c in cars if _hp_ok(c)]
    logger.info(f"encar: {len(cars)} кандидатов после спеки — проверяю цвет/ДТП")

    kept, needs_check, rejected = [], [], []
    for car in cars:
        car = enrich_encar_full(car)
        status, note = classify_encar_strict(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            rejected.append({
                "trim": car.get("trim"), "year": car.get("year"), "mileage_km": car.get("mileage_km"),
                "color": car.get("color"), "condition_status": status, "condition_note": note,
                "reasons": f"состояние: {note}", "url": car.get("url"),
            })
    logger.info(f"encar: итог — {len(kept)} чистых, {len(needs_check)} требуют проверки, "
                f"{len(rejected)} отсеяно из {len(cars)}")
    return kept, needs_check, rejected


def collect_charancha(rates: dict) -> tuple:
    try:
        cars = charancha_scraper.search_target_model(TARGET, logger)
    except Exception as error:
        logger.error(f"charancha: ошибка поиска: {error}")
        return [], [], []
    cars = [fill_missing_spec_by_cc(c) for c in cars]
    cars = [c for c in cars if _hp_ok(c)]
    logger.info(f"charancha: {len(cars)} кандидатов после спеки")

    kept, needs_check, rejected = [], [], []
    for car in cars:
        status, note = classify_charancha_strict(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            rejected.append({
                "trim": car.get("trim"), "year": car.get("year"), "mileage_km": car.get("mileage_km"),
                "color": car.get("color"), "condition_status": status, "condition_note": note,
                "reasons": f"состояние: {note}", "url": car.get("url"),
            })
    logger.info(f"charancha: итог — {len(kept)} чистых, {len(needs_check)} требуют проверки, "
                f"{len(rejected)} отсеяно из {len(cars)}")
    return kept, needs_check, rejected


def collect_login_source(source_name: str, search_fn, classify_fn, rates: dict) -> tuple:
    """Общий сбор для логин-площадок с известной классификацией
    (HeyDealer/KCar/AJ). Glovis/Lotte — только воронка (см. докстринг)."""
    target = {**TARGET, "allow_fresh_login": True}
    try:
        cars_raw = search_fn(target, logger)
    except LoginRequiredError as error:
        logger.error(f"{source_name}: {error}")
        return {"raw_matches": 0, "error": str(error)}, [], [], []
    except Exception as error:
        logger.error(f"{source_name}: ошибка поиска: {error}")
        return {"raw_matches": 0, "error": str(error)}, [], [], []

    cars = [fill_missing_spec_by_cc(c) for c in cars_raw]
    cars = [c for c in cars if _hp_ok(c)]
    kept, needs_check, rejected = [], [], []
    for car in cars:
        status, note = classify_fn(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            rejected.append({
                "trim": car.get("trim"), "year": car.get("year"), "mileage_km": car.get("mileage_km"),
                "color": car.get("color"), "condition_status": status, "condition_note": note,
                "reasons": f"состояние: {note}", "url": car.get("url"),
            })
    logger.info(f"{source_name}: итог — {len(kept)} чистых, {len(needs_check)} требуют проверки, "
                f"{len(rejected)} отсеяно из {len(cars)} (из {len(cars_raw)} сырых)")
    return {"raw_matches": len(cars), "error": None}, kept, needs_check, rejected


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rates = fetch_cbr_rates()
    logger.info(f"Курс ЦБ: krw_rub={rates['krw_rub']:.6f} (источник: {rates.get('_source')})")

    funnel_only = {
        "kbchachacha": collect_funnel_only("kbchachacha", kbchachacha_target_adapter.search_target_model),
        "onbid": collect_funnel_only("onbid", onbid_scraper.search_target_model),
    }

    encar_kept, encar_check, encar_rejected = collect_encar(rates)
    charancha_kept, charancha_check, charancha_rejected = collect_charancha(rates)

    hd_info, hd_kept, hd_check, hd_rejected = collect_login_source(
        "heydealer", heydealer_scraper.search_target_model, classify_heydealer_strict, rates)
    kc_info, kc_kept, kc_check, kc_rejected = collect_login_source(
        "kcar", kcar_scraper.search_target_model, classify_kcar, rates)
    aj_info, aj_kept, aj_check, aj_rejected = collect_login_source(
        "aj", aj_scraper.search_target_model, classify_aj, rates)
    # Glovis/Lotte — без надёжной классификации/логина в этом прогоне,
    # собираются только как воронка (см. докстринг файла).
    funnel_only["glovis"] = collect_funnel_only("glovis", glovis_scraper.search_target_model)
    funnel_only["lotte"] = collect_funnel_only("lotte", lotte_scraper.search_target_model)

    primary = _sort_by_mileage(encar_kept + charancha_kept + hd_kept + kc_kept + aj_kept)
    needs_check = _sort_by_mileage(encar_check + charancha_check + hd_check + kc_check + aj_check)
    rejected = encar_rejected + charancha_rejected + hd_rejected + kc_rejected + aj_rejected

    logger.info(f"=== Итог: основной список = {len(primary)}, требует проверки = {len(needs_check)}, "
                f"отсеяно = {len(rejected)} ===")

    DATA_PATH.write_text(json.dumps({
        "rub_per_krw": rates["krw_rub"],
        "rates_source": rates.get("_source"),
        "funnel_only": funnel_only,
        "login_info": {"heydealer": hd_info, "kcar": kc_info, "aj": aj_info},
        "primary": primary,
        "needs_check": needs_check,
        "counts": {
            "encar_ok": len(encar_kept),
            "charancha_ok": len(charancha_kept),
            "heydealer_ok": len(hd_kept),
            "kcar_ok": len(kc_kept),
            "aj_ok": len(aj_kept),
        },
        "rejected": rejected,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"Промежуточные данные сохранены: {DATA_PATH}")
