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

ВАЖНО (добавлено 2026-07-20, вторая версия): раньше этот инструмент просто
показывал результат diversify() — round-robin отбор МОДЕЛЕЙ/источников, но
НЕ гарантию, что конкретный кандидат реально соберётся в пост (build_lead_post()
может вернуть None из-за нехватки данных, а validate_post_before_publish()
отклонить по фото/названию/году/пробегу/цене). main_pipeline.run() тоже
НЕ подстраховывается от этого — diversify(candidates, post_limit, ...)
отдаёт РОВНО post_limit кандидатов, и если часть из них не соберётся в
пост, реальный прогон опубликует МЕНЬШЕ post_limit постов, а не подберёт
замену из оставшегося пула. Поэтому теперь инструмент прогоняет каждого
кандидата через ТЕ ЖЕ проверки (build_lead_post + validate_post_before_publish)
и явно показывает, кто прошёл, а кто и почему отсеялся — это и есть
честный ответ на "сколько из N постов реально выйдет".

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
from lead_post_builder import build_lead_post
from post_text_builder import prepare_post_fields
from post_validator import validate_post_before_publish
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("plan_next_posts")

FRESHNESS_DAYS = 14
# Берём с запасом от diversify() (не только --limit) — реальный
# main_pipeline.run() ТОЖЕ этого не делает (см. докстринг выше, это
# известное свойство, не баг данного инструмента), но здесь запас нужен,
# чтобы после отсева честно показать, СКОЛЬКО кандидатов нужно было бы
# пройти дальше по пулу, чтобы реально набрать --limit постов.
POOL_MULTIPLIER = 4


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

    # Пул с запасом (см. POOL_MULTIPLIER) — именно РОВНО --limit кандидатов
    # получил бы реальный main_pipeline.run(), но здесь нужен запас, чтобы
    # после отсева по гейту честно посчитать, скольким из ОСТАВШЕГОСЯ пула
    # пришлось бы дойти, чтобы реально набрать --limit постов.
    pool_limit = args.limit * POOL_MULTIPLIER
    candidates = diversify(candidates, pool_limit, MAX_PER_MODEL)

    recent_brands = recent_posted_brand_sequence(pub_log, config.MAX_SAME_BRAND_IN_A_ROW)
    candidates = limit_consecutive_brand(candidates, recent_brands, config.MAX_SAME_BRAND_IN_A_ROW)

    if not candidates:
        _safe_print("(пусто — нет кандидатов после всех фильтров)")
        return

    rates = fetch_cbr_rates()

    # ── Часть 1: РОВНО то, что реально опубликовал бы main_pipeline.run()
    # с post_limit=--limit (diversify() уже отдал бы это же самое, без
    # запаса) — прогоняем через гейт и показываем, кто прошёл/отсеялся. ──
    _safe_print("")
    _safe_print(f"=== Первые {args.limit} кандидатов от diversify() (ровно то, что попробовал бы "
                f"реальный прогон с post_limit={args.limit}) ===")
    passed_in_first_n = 0
    for i, car in enumerate(candidates[:args.limit], start=1):
        post_text = build_lead_post(car, rates)
        label = f"[{car.get('source', '?')}] {car.get('brand', '')} {car.get('model', '')} {car.get('trim', '')}".strip()
        if post_text is None:
            _safe_print(f"{i}. ОТСЕЯН (build_lead_post: не хватает данных для цены под ключ) — {label}")
            continue

        fields, _ = prepare_post_fields(car, rates)
        validated, reason = validate_post_before_publish(car, fields)
        if validated is None:
            _safe_print(f"{i}. ОТСЕЯН (гейт: {reason}) — {label}")
            continue

        passed_in_first_n += 1
        _safe_print(f"{i}. ПРОЙДЁТ — {label} — {car.get('year', '?')}, {car.get('mileage_km', '?')} км "
                    f"— {car.get('url', '')}")

    _safe_print("")
    _safe_print(f"Итого: {passed_in_first_n}/{args.limit} реально опубликовались бы с этим пулом "
                f"кандидатов (main_pipeline.run() НЕ подбирает замену отсеянным — см. докстринг файла).")

    # ── Часть 2: если прогнать ВЕСЬ пул (с запасом) — сколько реально
    # нужно кандидатов, чтобы набрать --limit постов. Полезно для оценки,
    # стоит ли поднимать post_limit/пул с запасом в самом main_pipeline.py. ──
    _safe_print("")
    _safe_print(f"=== Если бы main_pipeline брал кандидатов из пула ×{POOL_MULTIPLIER}, "
                f"пока не наберёт {args.limit} постов ===")
    passed_total = []
    for car in candidates:
        if len(passed_total) >= args.limit:
            break
        post_text = build_lead_post(car, rates)
        if post_text is None:
            continue
        fields, _ = prepare_post_fields(car, rates)
        validated, reason = validate_post_before_publish(car, fields)
        if validated is None:
            continue
        passed_total.append(car)

    for i, car in enumerate(passed_total, start=1):
        _safe_print(f"{i}. [{car.get('source', '?')}] {car.get('brand', '')} {car.get('model', '')} "
                    f"{car.get('trim', '')} — {car.get('year', '?')}, {car.get('mileage_km', '?')} км "
                    f"— {car.get('url', '')}")

    if len(passed_total) < args.limit:
        _safe_print(f"\nДаже с запасом ×{POOL_MULTIPLIER} набралось только {len(passed_total)}/{args.limit} — "
                    f"кандидатов, реально проходящих гейт, сейчас меньше, чем нужно.")


if __name__ == "__main__":
    run()
