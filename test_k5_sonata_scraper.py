# -*- coding: utf-8 -*-
"""
test_k5_sonata_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает
полные данные (все фото + страховая история) по объявлениям Kia K5 и
Hyundai Sonata DN8 с kbchachacha.com для сравнения источников.

Фильтры (заданы в задаче):
    - 2022-2023 год выпуска
    - бензин 2.0, до 160 л.с.
    - пробег до 60 000 км

НЕ трогает used_car_scraper.py — только переиспользует его функции поиска
(parse_all_pages — с полной пагинацией по всем страницам) и новый
kb_detail_scraper.py для детальных страниц. Результат сохраняется в
test_k5_sonata/<carSeq>.json.

Полный масштаб: обрабатываются ВСЕ объявления, подходящие под фильтры
(не ограничено 2-3 штуками) — предварительная оценка показала ~60 штук
на обе модели вместе, детальный парсинг займёт по времени десятки минут
(на каждое объявление — отдельный заход браузера Playwright).

Запуск:
    python test_k5_sonata_scraper.py
"""

import json
import logging
import time
from pathlib import Path

from used_car_scraper import parse_all_pages
from kb_detail_scraper import fetch_car_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_k5_sonata")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_k5_sonata"

MAX_MILEAGE_KM = 60000
YEARS_WANTED = {2022, 2023}

# Коды makerCode/classCode/carCode найдены через живой разбор фильтров
# сайта (см. /public/search/carMaker.json и app.setClassCode/app.setMakerCode
# в браузере) — по каждой модели два carCode: "старое" поколение до
# рестайлинга 2023 года и "новое" (23년~현재), т.к. авто 2022-2023 года
# могут попасть в любой из двух бакетов сайта в зависимости от даты
# регистрации.
SEARCHES = {
    "Kia K5": [
        {"makerCode": 102, "classCode": 1225, "carCode": 3051, "regiDay": "2022,2023"},  # K5 3세대 (19~23)
        {"makerCode": 102, "classCode": 1225, "carCode": 3374, "regiDay": "2022,2023"},  # 더 뉴 K5 3세대 (23~현재)
    ],
    "Hyundai Sonata DN8": [
        {"makerCode": 101, "classCode": 1108, "carCode": 2994, "regiDay": "2022,2023"},  # 쏘나타 (DN8) (19~23)
        {"makerCode": 101, "classCode": 1108, "carCode": 3330, "regiDay": "2022,2023"},  # 쏘나타 디 엣지(DN8) (23~현재)
    ],
}

# Título должен содержать "2.0" (бензиновый атмосферный) и НЕ содержать
# ничего из этого списка (турбо/гибрид/LPG/дизель/спортивная N Line —
# другие моторы/типы топлива, не то, что просили в задаче).
EXCLUDE_KEYWORDS = ["터보", "하이브리드", "LPG", "LPI", "디젤", "N Line", "1.6", " T "]


def matches_filters(car: dict) -> bool:
    title = car.get("model", "")
    if "2.0" not in title:
        return False
    if any(bad in title for bad in EXCLUDE_KEYWORDS):
        return False
    if car.get("year") not in YEARS_WANTED:
        return False
    mileage = car.get("mileage_km")
    if mileage is None or mileage > MAX_MILEAGE_KM:
        return False
    return True


def collect_candidates(model_label: str, search_param_variants: list) -> list:
    """Ищет по всем вариантам carCode для модели (полная пагинация по
    каждому варианту через parse_all_pages), возвращает подходящие под
    фильтры объявления (без дублей по carSeq)."""
    seen_ids = set()
    candidates = []
    total_raw = 0

    for params in search_param_variants:
        try:
            cars = parse_all_pages(params, max_pages=25, delay=0.5)
        except Exception as error:
            logger.error(f"[{model_label}] не удалось загрузить список ({params}): {error}")
            continue

        total_raw += len(cars)
        logger.info(f"[{model_label}] carCode={params['carCode']}: просмотрено {len(cars)} объявлений")

        for car in cars:
            if car["external_id"] in seen_ids:
                continue
            if matches_filters(car):
                seen_ids.add(car["external_id"])
                candidates.append(car)

    logger.info(f"[{model_label}] всего просмотрено {total_raw}, подходит под фильтры {len(candidates)}")
    return candidates


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    summary = {}

    for model_label, search_variants in SEARCHES.items():
        candidates = collect_candidates(model_label, search_variants)
        summary[model_label] = []

        for i, car in enumerate(candidates, start=1):
            logger.info(f"[{model_label}] ({i}/{len(candidates)}) детальная страница: {car['url']}")
            try:
                detail = fetch_car_detail(car["url"])
            except Exception as error:
                logger.error(f"Не удалось получить детали {car['url']}: {error}")
                detail = {}

            record = {
                "model_label": model_label,
                **car,          # данные из списка поиска (title, year, mileage, price, url, ...)
                **detail,       # данные с детальной страницы (photos, accident_status, ...)
            }

            out_path = OUTPUT_DIR / f"{car['external_id']}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Сохранено: {out_path.name} (фото: {len(record.get('photos', []))})")

            summary[model_label].append(record)
            time.sleep(2)

    logger.info("=== Готово ===")
    for model_label, records in summary.items():
        logger.info(f"{model_label}: сохранено {len(records)} объявлений в {OUTPUT_DIR}")


if __name__ == "__main__":
    run()
