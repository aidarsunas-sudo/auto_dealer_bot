# -*- coding: utf-8 -*-
"""
build_bmw_x6_report.py — разовый отчёт (НЕ публикация) по BMW X6 (G06),
2026 модельного года, пробег ≤10 000 км, с корейских площадок
(Encar / HeyDealer / K Car / KB ChaChaCha).

Правила отбора (по ТЗ):
- ГОД: 2026 — основной блок. Если лотов 2026 MY нет — явный блок
  «2026: нет предложений» и ОТДЕЛЬНЫЙ блок 2025 MY (без подмены года).
- ПРОБЕГ ≤ 10 000 км, сортировка по возрастанию.
- БЕЗ ДТП (무사고 по 성능점검표): encar master.accdient == False,
  heydealer carhistory 0 аварий, kcar accident_free == True.
- БЕЗ СТРАХОВЫХ ВЫПЛАТ (보험처리 이력 = 0): любая история страховых
  случаев → «исключённые» с причиной.
- БЕЗ ОКРАСОВ (도색 이력 없ом): encar inspection.outers пуст;
  статус непроверяем → «требует проверки» (в основной список не входит).

Цена: вон + ₽ по курсу ЦБ + «под ключ» через customs_calculator
(та же логика calc_price, что build_bmw_x4_report.py — наценка отдельно).

Запуск:
    python build_bmw_x6_report.py
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

import encar_scraper
import heydealer_scraper
import kbchachacha_target_adapter
import kcar_scraper
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
logger = logging.getLogger("build_bmw_x6_report")

OUT_DIR = Path(__file__).resolve().parent / "bmw_x6_report"
DATA_PATH = OUT_DIR / "collected.json"

TARGET_2026 = {"brand": "BMW", "model": "X6", "year_from": 2026, "year_to": 2026, "mileage_max": 10000}
TARGET_2025 = {"brand": "BMW", "model": "X6", "year_from": 2025, "year_to": 2025, "mileage_max": 10000}


def _hp_ok(car: dict) -> bool:
    return car.get("hp") is not None


def simple_price_rub(price_krw, krw_rub: float) -> int | None:
    if not price_krw:
        return None
    return round(price_krw * krw_rub)


def calc_price(car: dict, rates: dict) -> dict | None:
    """Полная разбивка «под ключ Казань» — копия calc_price из
    build_bmw_x4_report.py (post_builder-логика, наценка отдельной строкой)."""
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

    return {
        "total_rub": int(result["total_with_car"] + bank_commission + extra_costs),
        "total_with_margin_rub": int(result["total_with_car"] + bank_commission +
                                     extra_costs + config.MARGIN_RUB),
        "car_price_rub": int(result["price_rub"]),
        "bank_commission_rub": int(bank_commission),
        "fee_rub": int(result["fee_rub"]),
        "ets_rub": int(result["ets_rub"]),
        "util_rub": int(result["util_rub"]),
        "logistics_rub": int(config.LOGISTICS_RUB),
        "broker_rub": int(config.BROKER_FEE_RUB),
        "margin_rub": int(config.MARGIN_RUB),
        "age_bucket": age,
    }


def fill_missing_spec_by_cc(car: dict) -> dict:
    """Копия fallback из build_compact_suv_2022_report.py — если find_spec()
    не сматчился, но cc известен (Charancha/KCar), и для (модель, cc) есть
    РОВНО ОДНА спека — берём hp/fuel оттуда (не гадаем)."""
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


# ═══ Классификация по ТЗ: clean / unknown (требует проверки) / excluded ─────

def classify_encar(car: dict) -> tuple:
    if not car.get("_diagnosis_ok"):
        return "unknown", "성능점검표/диагностика недоступны — статус не проверен"
    if car.get("_waterlog"):
        return "excluded", "потоп (техпаспорт)"
    if car.get("_accdient_flag") is True:
        return "excluded", "подтверждённое ДТП (техпаспорт)"
    panels = car.get("_outers_panels") or []
    if panels:
        return "excluded", f"окрасы/ремонт ({len(panels)} панелей: {', '.join(panels)})"
    ins = car.get("_insurance")
    if ins is None:
        return "unknown", "страховая история (보험처리 이력) недоступна — проверить вручную"
    if ins["count"] > 0:
        return "excluded", f"страховые выплаты: {ins['count']} случ. на {ins['cost_krw']:,} ₩"
    return "clean", "무사고: без ДТП, окрасов и страховых выплат (성능점검표)"


def classify_heydealer(car: dict) -> tuple:
    accident_free = car.get("accident_free")
    notes = car.get("damage_notes") or ""
    if accident_free is None:
        return "unknown", "нет числа страховых случаев в данных площадки — проверить вручную"
    if accident_free is True:
        return "clean", "0 страховых случаев (보험0건), без ДТП"
    return "excluded", f"страховые выплаты есть ({notes}) — по ТЗ исключение"


def classify_kcar(car: dict) -> tuple:
    accident_free = car.get("accident_free")
    if accident_free is None:
        return "unknown", "статус ДТП/страховых не указан площадкой — проверить вручную"
    if accident_free is True:
        return "clean", "без ДТП (по данным площадки)"
    return "excluded", f"площадка отмечает повреждения: {car.get('damage_notes') or '(без деталей)'}"


# ═══ Encar: обогащение целиком (фото, цвет, VIN, диагностика+inspection,
# страховая история из diagnosis) ────────────────────────────────────────────

def _extract_insurance(diagnosis: dict) -> dict | None:
    """보험처리 이력 из 성능점검표: diagnosis отдаёт структуру с историей
    страховых ремонтов (ключи отличаются по формам — ищем списки с суммами).
    Возвращает {"count": int, "cost_krw": int} или None если не найдено."""
    if not diagnosis:
        return None
    blob = json.dumps(diagnosis, ensure_ascii=False)
    # типовая форма: список событий с ключами amount/claim/cost
    candidates = []
    def walk(o):
        if isinstance(o, dict):
            keys = {k.lower() for k in o.keys()}
            if keys & {"amount", "cost", "claim", "price", "won", "보험금"} and not isinstance(o.get("amount"), dict):
                candidates.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(diagnosis)
    if not candidates:
        # альтернатива: поля агрегаторов
        for agg_key in ("insurance", "insuranceHistory", "accidentHistory", "carhistory", "car_history"):
            node = diagnosis.get(agg_key) if isinstance(diagnosis, dict) else None
            if isinstance(node, dict):
                cnt = node.get("count") or node.get("totalCount") or node.get("accidentCount")
                cost = node.get("cost") or node.get("totalCost") or node.get("amount") or 0
                if cnt is not None:
                    return {"count": int(cnt), "cost_krw": int(cost or 0)}
        return None
    total = 0
    for c in candidates:
        for k in ("amount", "cost", "claim", "price", "won", "보험금"):
            if k in c and isinstance(c[k], (int, float)):
                total += c[k]
                break
    return {"count": len(candidates), "cost_krw": int(total)}


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
    diagnosis = extra.get("diagnosis") or {}

    car["_diagnosis_ok"] = bool(extra.get("inspection") is not None or diagnosis)
    car["_accdient_flag"] = master.get("accdient")
    car["_waterlog"] = master.get("waterlog")
    car["_outers_panels"] = [o.get("type", {}).get("title", "?") for o in outers]
    car["_insurance"] = _extract_insurance(diagnosis)
    time.sleep(0.4)
    return car


def collect_encar(target: dict, rates: dict) -> tuple:
    try:
        cars = encar_scraper.search_target_model(target, logger)
    except Exception as error:
        logger.error(f"encar: ошибка поиска: {error}")
        return [], [], [], f"ошибка поиска: {error}"
    cars = [fill_missing_spec_by_cc(c) for c in cars]
    cars = [c for c in cars if _hp_ok(c)]
    logger.info(f"encar: {len(cars)} кандидатов {target['year_from']} после спеки")

    kept, needs_check, excluded = [], [], []
    for car in cars:
        car = enrich_encar_full(car)
        status, note = classify_encar(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            excluded.append({"source": "encar", "trim": car.get("trim"), "year": car.get("year"),
                             "mileage_km": car.get("mileage_km"), "color": car.get("color"),
                             "reasons": note, "url": car.get("url")})
    logger.info(f"encar {target['year_from']}: {len(kept)} чистых, {len(needs_check)} проверки, {len(excluded)} исключены")
    return kept, needs_check, excluded, None


def collect_heydealer(target: dict, rates: dict) -> tuple:
    hd_target = {**target, "allow_fresh_login": True}
    try:
        cars = heydealer_scraper.search_target_model(hd_target, logger)
    except LoginRequiredError as error:
        logger.error(f"heydealer: {error}")
        return [], [], [], str(error)
    except Exception as error:
        logger.error(f"heydealer: ошибка поиска: {error}")
        return [], [], [], str(error)
    cars = [fill_missing_spec_by_cc(c) for c in cars]
    cars = [c for c in cars if _hp_ok(c)]

    kept, needs_check, excluded = [], [], []
    for car in cars:
        status, note = classify_heydealer(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            excluded.append({"source": "heydealer", "trim": car.get("trim"), "year": car.get("year"),
                             "mileage_km": car.get("mileage_km"), "reasons": note, "url": car.get("url")})
    logger.info(f"heydealer {target['year_from']}: {len(kept)} чистых, {len(needs_check)} проверки, {len(excluded)} исключены")
    return kept, needs_check, excluded, None


def collect_kcar(target: dict, rates: dict) -> tuple:
    kc_target = {**target, "allow_fresh_login": True}
    try:
        cars = kcar_scraper.search_target_model(kc_target, logger)
    except LoginRequiredError as error:
        return [], [], [], str(error)
    except Exception as error:
        return [], [], [], str(error)
    cars = [fill_missing_spec_by_cc(c) for c in cars]
    cars = [c for c in cars if _hp_ok(c)]

    kept, needs_check, excluded = [], [], []
    for car in cars:
        status, note = classify_kcar(car)
        car["_condition_status"] = status
        car["_condition_note"] = note
        car["_price_rub"] = simple_price_rub(car.get("price"), rates["krw_rub"])
        car["_price_details"] = calc_price(car, rates)
        if status == "clean":
            kept.append(car)
        elif status == "unknown":
            needs_check.append(car)
        else:
            excluded.append({"source": "kcar", "trim": car.get("trim"), "year": car.get("year"),
                             "mileage_km": car.get("mileage_km"), "reasons": note, "url": car.get("url")})
    logger.info(f"kcar {target['year_from']}: {len(kept)} чистых, {len(needs_check)} проверки, {len(excluded)} исключены")
    return kept, needs_check, excluded, None


def collect_kb_funnel(target: dict) -> dict:
    try:
        cars = kbchachacha_target_adapter.search_target_model(target, logger)
    except Exception as error:
        return {"raw_matches": 0, "error": str(error)}
    return {"raw_matches": len([c for c in cars if _hp_ok(fill_missing_spec_by_cc(c))]), "error": None}


def _sort(cars: list) -> list:
    return sorted(cars, key=lambda c: (c.get("mileage_km") is None, c.get("mileage_km") or 0))


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rates = fetch_cbr_rates()
    logger.info(f"Курс ЦБ: krw_rub={rates['krw_rub']:.6f} (источник: {rates.get('_source')})")

    result = {"rates": {k: rates[k] for k in ("krw_rub", "usd_rub", "eur_rub", "cny_rub")},
              "rates_source": rates.get("_source"), "sources": {}}

    for label, target in (("main_2026", TARGET_2026), ("alt_2025", TARGET_2025)):
        kept, needs_check, excluded = [], [], []
        e_kept, e_check, e_exc, e_err = collect_encar(target, rates)
        h_kept, h_check, h_exc, h_err = collect_heydealer(target, rates)
        k_kept, k_check, k_exc, k_err = collect_kcar(target, rates)
        for c in e_kept + h_kept + k_kept:
            c["_year_block"] = label
        for c in e_check + h_check + k_check:
            c["_year_block"] = label
        kept = _sort(e_kept + h_kept + k_kept)
        needs_check = _sort(e_check + h_check + k_check)
        excluded = e_exc + h_exc + k_exc
        result[label] = {"kept": kept, "needs_check": needs_check, "excluded": excluded}
        result["sources"][label] = {
            "encar": {"error": e_err}, "heydealer": {"error": h_err}, "kcar": {"error": k_err},
            "kbchachacha": collect_kb_funnel(target),
        }
        logger.info(f"=== {label}: основной {len(kept)}, проверка {len(needs_check)}, исключены {len(excluded)} ===")

    DATA_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"Данные сохранены: {DATA_PATH}")
