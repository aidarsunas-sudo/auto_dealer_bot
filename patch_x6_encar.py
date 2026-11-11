# -*- coding: utf-8 -*-
"""
patch_x6_encar.py — догрузка Encar для отчёта bmw_x6_report без Playwright.

Проблема: резолвер encar_scraper._lookup_codes ходит Playwright'ом на
fc_carsearchlist.do, а страница из этой сети сегодня не догружает DOM
(goto timeout, проверено 2 раза 11.11.2026) — при этом сам JSON API
api.encar.com/search/car/list/general работает. Резолвинг для X6 тривиален
(импорт, ModelGroup текстом "X6" — тот же паттерн, что подтверждён для X4),
поэтому сюда СИМЕНТИРУЕТСЯ положительный кэш резолвинга — дальше
search_target_model() идёт по обычному API-пути, без браузера.

Запуск:
    python patch_x6_encar.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import build_bmw_x6_report as builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("patch_x6_encar")

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "target_models_cache.json"
DATA_PATH = ROOT / "bmw_x6_report" / "collected.json"

SEED = {"car_type": "A", "manufacturer_ko": "BMW", "model_group": "X6"}


def seed_cache() -> None:
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    cache["encar:BMW:X6"] = {"result": SEED, "resolved_at": datetime.now(timezone.utc).isoformat()}
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Кэш посеян: encar:BMW:X6 -> {SEED}")


if __name__ == "__main__":
    seed_cache()

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    rates = {**data["rates"], "_source": data.get("rates_source")}

    for block_key, target in (("main_2026", builder.TARGET_2026), ("alt_2025", builder.TARGET_2025)):
        kept, needs_check, excluded, err = builder.collect_encar(target, rates)
        block = data.setdefault(block_key, {"kept": [], "needs_check": [], "excluded": []})
        # заменяем encar-часть, остальные площадки не трогаем
        block["kept"] = [c for c in block["kept"] if c.get("source") != "encar"] + kept
        block["needs_check"] = [c for c in block["needs_check"] if c.get("source") != "encar"] + needs_check
        block["excluded"] = [e for e in block["excluded"] if e.get("source") != "encar"] + excluded
        block["kept"] = builder._sort(block["kept"])
        block["needs_check"] = builder._sort(block["needs_check"])
        data["sources"][block_key]["encar"] = {"error": err}
        logger.info(f"{block_key}: encar — {len(kept)} чистых, {len(needs_check)} проверки, {len(excluded)} исключены")

    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"Патч сохранён: {DATA_PATH}")
