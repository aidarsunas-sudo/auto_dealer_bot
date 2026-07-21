# -*- coding: utf-8 -*-
"""
model_search_test.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает полные данные
(все фото + страховая история) по объявлениям kbchachacha.com для ЛЮБОЙ модели,
заданной в TARGETS ниже — по образцу test_k5_sonata_scraper.py (Kia K5 /
Hyundai Sonata), но без хардкода под конкретную марку.

НЕ трогает used_car_scraper.py и kb_detail_scraper.py — только переиспользует
их (parse_all_pages — полная пагинация, fetch_car_detail — детальная страница).
Результат сохраняется в model_search_results/<slug модели>/<carSeq>.json.

Как добавить новую модель в TARGETS (makerCode/classCode/carCode вручную
не подобрать — они специфичны для kbchachacha и находятся так):
    1. GET https://www.kbchachacha.com/public/search/carMaker.json
       -> найти нужную марку в result["수입"] (иномарки) или result["국산"]
          (корейские марки), взять её "makerCode".
    2. GET https://www.kbchachacha.com/public/search/carClass.json?makerCode=<N>
       -> result["code"] — список моделей марки, каждая с classCode/carCode.
          Некоторые модели разбиты на НЕСКОЛЬКО carCode (до/после рестайлинга) —
          проверить все совпадения по className.
    3. GET https://www.kbchachacha.com/public/search/carModel.json?makerCode=<N>&classCode=<N>&carCode=<N>
       -> result["codeModel"] — список модификаций (modelName) с реальными
          названиями комплектаций (напр. "35 TDI", "40 TFSI Quattro") — по ним
          удобнее фильтровать бензин/дизель/гибрид, чем по "2.0" в заголовке
          (иномарки часто НЕ показывают объём в заголовке объявления, в
          отличие от Kia/Hyundai в исходном test_k5_sonata_scraper.py).

Запуск:
    python model_search_test.py                 # полный прогон по всем TARGETS
    python model_search_test.py --limit 3        # тест: не больше 3 объявлений
                                                  # на модель (staged workflow —
                                                  # сначала проверить структуру
                                                  # данных на малой выборке)
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

from used_car_scraper import parse_all_pages
from kb_detail_scraper import fetch_car_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("model_search_test")

OUTPUT_ROOT = Path(__file__).resolve().parent / "model_search_results"

# ─── Здесь описываются искомые модели и фильтры — правьте вручную под задачу ───
TARGETS = [
    {
        "label": "Audi A4 2.0 D",
        # Audi A4 (B9), 2016~настоящее время — один carCode на всё поколение
        # (в отличие от K5/Sonata, где рестайлинг 2023 разбивал на 2 carCode).
        "kbchachacha_search_variants": [
            {"makerCode": 109, "classCode": 1942, "carCode": 2982, "regiDay": "2022,2023"},
        ],
        "years_wanted": {2022, 2023},
        "max_mileage_km": 60000,
        # Комплектация должна содержать любое из этих слов. "30 TDI"/"35 TDI"/
        # "35 TDI Quattro" — подтверждённые 2.0 дизель бэджи A4 (B9). "40 TDI
        # Quattro"/"55 TDI Quattro" сюда намеренно НЕ включены — по решению
        # пользователя (2026-07-05), т.к. объём их двигателя не подтверждён.
        "require_any_keywords": ["30 TDI", "35 TDI"],
        "exclude_keywords": ["TFSI", "e-tron", "하이브리드", "PHEV"],
        "require_no_accident": True,  # отбрасывать объявления с accident_status "사고있음"
    },
    # Добавляйте новые словари сюда по этому же образцу.
]


def slugify(label: str) -> str:
    return re.sub(r"[^\w]+", "_", label.strip().lower()).strip("_")


def matches_filters(car: dict, target: dict) -> bool:
    title = car.get("model", "")

    require_any = target.get("require_any_keywords")
    if require_any and not any(kw in title for kw in require_any):
        return False

    if any(kw in title for kw in target.get("exclude_keywords", [])):
        return False

    if car.get("year") not in target["years_wanted"]:
        return False

    mileage = car.get("mileage_km")
    if mileage is None or mileage > target["max_mileage_km"]:
        return False

    return True


def collect_candidates(target: dict) -> list:
    """Ищет по всем вариантам carCode для модели (полная пагинация по
    каждому варианту через parse_all_pages), возвращает подходящие под
    фильтры объявления (без дублей по carSeq)."""
    label = target["label"]
    seen_ids = set()
    candidates = []
    total_raw = 0

    for params in target["kbchachacha_search_variants"]:
        try:
            cars = parse_all_pages(params, max_pages=25, delay=0.5)
        except Exception as error:
            logger.error(f"[{label}] не удалось загрузить список ({params}): {error}")
            continue

        total_raw += len(cars)
        logger.info(f"[{label}] carCode={params['carCode']}: просмотрено {len(cars)} объявлений")

        for car in cars:
            if car["external_id"] in seen_ids:
                continue
            if matches_filters(car, target):
                seen_ids.add(car["external_id"])
                candidates.append(car)

    logger.info(f"[{label}] всего просмотрено {total_raw}, подходит под фильтры {len(candidates)}")
    return candidates


def run(limit: int | None = None) -> None:
    summary = {}

    for target in TARGETS:
        label = target["label"]
        out_dir = OUTPUT_ROOT / slugify(label)
        out_dir.mkdir(parents=True, exist_ok=True)

        candidates = collect_candidates(target)
        if limit is not None:
            candidates = candidates[:limit]
            logger.info(f"[{label}] --limit {limit}: беру только первые {len(candidates)}")

        summary[label] = []

        for i, car in enumerate(candidates, start=1):
            logger.info(f"[{label}] ({i}/{len(candidates)}) детальная страница: {car['url']}")
            try:
                detail = fetch_car_detail(car["url"])
            except Exception as error:
                logger.error(f"Не удалось получить детали {car['url']}: {error}")
                detail = {}

            # Страница детально отдаёт accident_status не всегда с первого раза
            # (см. комментарий в kb_detail_scraper.py про нестабильную загрузку) —
            # если нужен фильтр по ДТП, а статус не пришёл, пробуем ещё раз перед
            # тем, как молча сохранить объявление без проверки истории.
            if target.get("require_no_accident") and "accident_status" not in detail:
                logger.warning(f"accident_status не пришёл с первого раза, повтор: {car['url']}")
                try:
                    detail = fetch_car_detail(car["url"])
                except Exception as error:
                    logger.error(f"Повтор не удался {car['url']}: {error}")

            if target.get("require_no_accident") and "accident_status" in detail:
                if "없음" not in detail["accident_status"]:
                    logger.info(f"Пропуск (найдено ДТП): {car['url']}")
                    continue

            record = {
                "model_label": label,
                **car,          # данные из списка поиска (title, year, mileage, price, url, ...)
                **detail,       # данные с детальной страницы (photos, accident_status, ...)
            }

            out_path = out_dir / f"{car['external_id']}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Сохранено: {out_path.name} (фото: {len(record.get('photos', []))})")

            summary[label].append(record)
            time.sleep(2)

    logger.info("=== Готово ===")
    for label, records in summary.items():
        logger.info(f"{label}: сохранено {len(records)} объявлений в {OUTPUT_ROOT / slugify(label)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="макс. число объявлений на модель (для тестового прогона)")
    args = parser.parse_args()
    run(limit=args.limit)
