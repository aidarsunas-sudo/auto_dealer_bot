# -*- coding: utf-8 -*-
"""
patch_x4_report_data.py — точечная догрузка/починка collected.json отчёта
bmw_x4_report без полного повторного прогона build_bmw_x4_report.py:

1) КУРС: если в collected.json rates_source != "cbr" (сбор шёл на запасных
   курсах из-за таймаута cbr-xml-daily.ru) — подставляет свежие курсы ЦБ,
   полученные вручную на дату сбора, и ПЕРЕСЧИТЫВАЕТ _price_rub и
   _price_details для всех машин (через calc_price самого билдера).
2) ENCAR: если encar-часть пустая (в прогоне сборки api.encar.com отдал 404 —
   временная блокировка) — повторяет поиск/обогащение/классификацию через
   collect_encar() билдера и вливает результат; при повторном сбое API
   поднимает ранее сохранённые лоты из model_search_results (прогон
   2026-11-10 ~16:37, полные данные) и помечает их _from_cached_run.

Запуск:
    python -X utf8 patch_x4_report_data.py
"""

import json
import logging
from pathlib import Path

import aj_scraper
import build_bmw_x4_report as builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("patch_x4_report_data")

DATA_PATH = Path(__file__).resolve().parent / "bmw_x4_report" / "collected.json"
CACHED_RUN_DIR = (Path(__file__).resolve().parent / "model_search_results"
                  / "bmw_x4_2024_2026_5000_accidentfree_minphotos3")

# Курсы ЦБ РФ на 2026-11-10 (cbr-xml-daily.ru/daily_json.js, получены
# вручную при подготовке отчёта — сборщик в тот момент не достучался).
CBR_RATES_2026_11_10 = {
    "krw_rub": 60.9423 / 1000,
    "usd_rub": 82.7536,
    "eur_rub": 92.3944,
    "cny_rub": 12.3098,
    "_source": "cbr",
}


def reprice_all(data: dict, rates: dict) -> None:
    for section in ("primary", "needs_check"):
        for car in data.get(section, []):
            car["_price_rub"] = builder.simple_price_rub(car.get("price"), rates["krw_rub"])
            car["_price_details"] = builder.calc_price(car, rates)
    data["rub_per_krw"] = rates["krw_rub"]
    data["rates_source"] = rates["_source"]
    logger.info("Цены пересчитаны по курсу ЦБ 2026-11-10")


def encar_is_empty(data: dict) -> bool:
    return not any(c.get("source") == "encar"
                   for section in ("primary", "needs_check") for c in data.get(section, []))


def merge_cached_encar(data: dict) -> int:
    """Fallback: поднимает Encar-лоты из прошлого удачного прогона
    model_search_results (полные JSON с фото/цветом/accident_free)."""
    added = 0
    for path in sorted(CACHED_RUN_DIR.glob("encar_*.json")):
        car = json.loads(path.read_text(encoding="utf-8"))
        if car.get("accident_free") is not True:
            continue  # непроверенные из кэша не поднимаем — статус не подтверждён
        car.setdefault("_condition_status", "clean")
        car.setdefault("_condition_note", "без окрасов/повреждений кузова (техпаспорт чист)")
        car["_from_cached_run"] = True
        data["primary"].append(car)
        added += 1
    data["primary"] = builder._sort_by_mileage(data["primary"])
    logger.info(f"Из кэша прогона поднято {added} проверенных Encar-лотов")
    return added


if __name__ == "__main__":
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    if data.get("rates_source") != "cbr":
        reprice_all(data, CBR_RATES_2026_11_10)

    if encar_is_empty(data):
        logger.info("Encar пуст в collected.json — пробую живой повтор через collect_encar()")
        try:
            kept, needs_check, rejected = builder.collect_encar(CBR_RATES_2026_11_10)
        except Exception as error:
            logger.error(f"collect_encar упал: {error}")
            kept, needs_check, rejected = [], [], []
        if kept or needs_check:
            data["primary"] = builder._sort_by_mileage(data["primary"] + kept)
            data["needs_check"] = builder._sort_by_mileage(data["needs_check"] + needs_check)
            data["rejected"] = (data.get("rejected") or []) + rejected
            data["counts"]["encar_ok"] = len(kept)
            logger.info(f"Encar догружен живьём: {len(kept)} чистых, {len(needs_check)} проверки")
        else:
            added = merge_cached_encar(data)
            data["counts"]["encar_ok"] = added

    # ─── AJ / SellCarAuction (логин починен: sellcarauction.co.kr) ────────────
    aj_error = data.get("login_info", {}).get("aj", {}).get("error")
    aj_has_cars = any(c.get("source") == "aj"
                      for section in ("primary", "needs_check") for c in data.get(section, []))
    if aj_error and not aj_has_cars:
        logger.info("AJ был недоступен в прогоне сборки — повторяю через починенный aj_scraper")
        aj_info, aj_kept, aj_check, aj_rejected = builder.collect_login_source(
            "aj", aj_scraper.search_target_model, builder.classify_aj, CBR_RATES_2026_11_10)
        data.setdefault("login_info", {})["aj"] = aj_info
        if aj_kept or aj_check:
            data["primary"] = builder._sort_by_mileage(data["primary"] + aj_kept)
            data["needs_check"] = builder._sort_by_mileage(data["needs_check"] + aj_check)
            data["rejected"] = (data.get("rejected") or []) + aj_rejected
        data["counts"]["aj_ok"] = len(aj_kept)
        logger.info(f"AJ догружен: {len(aj_kept)} чистых, {len(aj_check)} проверки, "
                    f"info={aj_info}")

    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"Патч сохранён: {DATA_PATH} (primary={len(data['primary'])}, "
                f"needs_check={len(data['needs_check'])})")
