# -*- coding: utf-8 -*-
"""
plan_next_posts.py — показывает, ЧТО опубликовал бы следующий прогон
main_pipeline.py (площадка + модель), НЕ публикуя ничего по-настоящему и
НЕ трогая published_log.json/target_models_cache.json/broad_search_state.json
(работает на КОПИИ published_log в памяти — реальные файлы не сохраняются).

Ровно та же цепочка, что и в main_pipeline.run(): сбор -> дедуп/свежесть/
пробег -> cooldown модели -> round-robin разнообразие -> cooldown марки —
но без публикации. Проверить, появилось ли разнообразие после фиксов
2026-07-20 (kbchachacha/charancha молчали неделями, K5 повторялся) — до
этого пришлось бы ждать реального прогона и смотреть в канал постфактум.

    python plan_next_posts.py
    python plan_next_posts.py --limit 10
"""

import argparse
import copy
import logging

import config
from target_collector import collect_target_model_cars
from broad_collector import collect_broad_cars
from published_log import load_log, select_candidates, recently_posted_models, recent_posted_brand_sequence
from candidate_diversity import diversify, filter_recent_models, limit_consecutive_brand, check_source_health, MAX_PER_MODEL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("plan_next_posts")

FRESHNESS_DAYS = 14


def _safe_print(text: str) -> None:
    import sys
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=5, help="Сколько постов показать (по умолчанию 5)")
    args = parser.parse_args()

    search_mode = config.SEARCH_MODE if config.SEARCH_MODE in ("targeted", "broad", "both") else "both"
    logger.info(f"Режим поиска: {search_mode}")

    cars = []
    if search_mode in ("targeted", "both"):
        cars += collect_target_model_cars(logger)
    if search_mode in ("broad", "both"):
        cars += collect_broad_cars(logger)

    if not cars:
        _safe_print("Ничего не найдено ни в одном из активных режимов поиска.")
        return

    check_source_health(cars, logger)

    # Копия — не сохраняем обратно, это только план, не реальный прогон.
    pub_log = copy.deepcopy(load_log())
    candidates = select_candidates(cars, FRESHNESS_DAYS, pub_log, config.MAX_MILEAGE_KM)

    recent_models = recently_posted_models(pub_log, config.RECENT_MODEL_DAYS)
    candidates = filter_recent_models(candidates, recent_models)

    candidates = diversify(candidates, args.limit, MAX_PER_MODEL)

    recent_brands = recent_posted_brand_sequence(pub_log, config.MAX_SAME_BRAND_IN_A_ROW)
    candidates = limit_consecutive_brand(candidates, recent_brands, config.MAX_SAME_BRAND_IN_A_ROW)

    _safe_print("")
    _safe_print(f"=== План следующих {min(args.limit, len(candidates))} постов ===")
    if not candidates:
        _safe_print("(пусто — нет кандидатов после всех фильтров)")
        return

    for i, car in enumerate(candidates[:args.limit], start=1):
        _safe_print(f"{i}. [{car.get('source', '?')}] {car.get('brand', '')} {car.get('model', '')} "
                    f"{car.get('trim', '')} — {car.get('year', '?')}, {car.get('mileage_km', '?')} км "
                    f"— {car.get('url', '')}")


if __name__ == "__main__":
    run()
