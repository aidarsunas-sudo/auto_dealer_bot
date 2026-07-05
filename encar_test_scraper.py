# -*- coding: utf-8 -*-
"""
encar_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает данные
Kia K5 / Hyundai Sonata DN8 с encar.com (обычный, НЕ аукцион) для сравнения
с kbchachacha (см. test_k5_sonata_scraper.py).

Encar устроен ПРОЩЕ и НАДЁЖНЕЕ, чем kbchachacha:
- Список объявлений — чистый JSON REST API (api.encar.com/search/car/list/premium),
  без HTML-парсинга вообще. Фильтры по марке/модели/топливу/пробегу — через
  query-язык вида "(And.Manufacturer.기아._.Model.K5.)". Пробег (Mileage)
  фильтруется НА СТОРОНЕ СЕРВЕРА через "Mileage.range(..60000)" — это резко
  сокращает объём, который приходится пролистывать (иначе пришлось бы
  пройти тысячи старых объявлений, отсортированных по дате изменения).
  Год выпуска фильтруем уже на своей стороне (FormYear) — серверный
  Year.range(...) вёл себя нестабильно при проверке, разбираться не стали.
- Полная галерея фото — ОТДЕЛЬНЫЙ запрос на детальную карточку:
  /v1/readside/vehicle/{id}?include=PHOTOS
  (в списке поиска отдаётся только 4 "витринных" фото — это выяснили и
  починили в этой сессии).
- Диагностика — тоже чистый JSON:
  /v1/readside/diagnosis/vehicle/{id}   — посегментная проверка кузова
                                           (каждая деталь: HOOD/TRUNK_LID/...
                                           со статусом NORMAL/повреждение)
  /v1/readside/inspection/vehicle/{id}  — тех.паспорт: VIN, флаг
                                           аварии/потопа/тюнинга, пробег,
                                           а в разделе "outers" — конкретные
                                           повреждённые панели кузова с кодом
                                           ремонта (например "판금/용접" —
                                           сваркой/жестянкой).
  Подробнее, чем на kbchachacha (там только 4 общих текстовых поля истории).

Коды Manufacturer/Model найдены вручную через живой перебор (сайт делит
одну модель на разные Model-строки по поколению/рестайлингу — так же,
как kbchachacha делит по carCode):
    기아 + "K5 3세대"             -> базовое 3-е поколение (до рестайлинга 2023)
    기아 + "더 뉴 K5 3세대"        -> рестайлинг (23 год~настоящее время)
    현대 + "쏘나타 (DN8)"         -> базовый DN8 (до рестайлинга)
    현대 + "쏘나타 디 엣지(DN8)"   -> рестайлинг "디 엣지"
"""

import json
import logging
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("encar_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_k5_sonata"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
SEARCH_URL = "https://api.encar.com/search/car/list/premium"
DIAGNOSIS_URL = "https://api.encar.com/v1/readside/diagnosis/vehicle/{id}"
INSPECTION_URL = "https://api.encar.com/v1/readside/inspection/vehicle/{id}"
DETAIL_URL = "https://api.encar.com/v1/readside/vehicle/{id}"
PHOTO_CDN_BASE = "https://ci.encar.com"

MAX_MILEAGE_KM = 60000
YEARS_WANTED = {"2022", "2023"}
BAD_BADGE_KEYWORDS = ["터보", "LPG", "LPI", "하이브리드"]

PAGE_SIZE = 200  # сколько объявлений запрашивать за один вызов при пагинации

# (label, manufacturer, model) — по каждой модели два "поколенческих" бакета.
SEARCHES = {
    "Kia K5": [("기아", "K5 3세대"), ("기아", "더 뉴 K5 3세대")],
    "Hyundai Sonata DN8": [("현대", "쏘나타 (DN8)"), ("현대", "쏘나타 디 엣지(DN8)")],
}


def search_model_all(manufacturer: str, model: str) -> list:
    """
    Достаёт ВСЕ объявления по manufacturer+model+가솔린+пробег<=60000,
    постранично (offset/limit), пока не дойдёт до конца.
    """
    q = (
        f"(And.Hidden.N._.CarType.Y._.Manufacturer.{manufacturer}._.Model.{model}."
        f"_.FuelType.가솔린._.Mileage.range(..{MAX_MILEAGE_KM}).)"
    )
    all_results = []
    offset = 0

    while True:
        resp = requests.get(
            SEARCH_URL,
            params={"count": "true", "q": q, "sr": f"|ModifiedDate|{offset}|{PAGE_SIZE}"},
            headers=HEADERS, timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        count = data.get("Count", 0)
        results = data.get("SearchResults", [])
        all_results.extend(results)

        offset += PAGE_SIZE
        if offset >= count or not results:
            break
        time.sleep(0.3)

    return all_results


def matches_filters(item: dict) -> bool:
    badge = item.get("Badge", "")
    if any(bad in badge for bad in BAD_BADGE_KEYWORDS):
        return False
    if item.get("FormYear") not in YEARS_WANTED:
        return False
    return True


def fetch_full_photos(car_id: str) -> list:
    """Полная галерея фото — отдельный запрос (в списке поиска только 4 витринных)."""
    try:
        r = requests.get(DETAIL_URL.format(id=car_id), params={"include": "PHOTOS"}, headers=HEADERS, timeout=20)
        r.raise_for_status()
        photos = r.json().get("photos") or []
        return [f"{PHOTO_CDN_BASE}{p['path']}" for p in photos if p.get("path")]
    except Exception as error:
        logger.warning(f"Не удалось получить полную галерею для {car_id}: {error}")
        return []


def fetch_diagnosis_and_inspection(car_id: str) -> dict:
    result = {}
    try:
        r = requests.get(DIAGNOSIS_URL.format(id=car_id), headers=HEADERS, timeout=20)
        if r.ok:
            result["diagnosis"] = r.json()
    except Exception as error:
        logger.warning(f"Не удалось получить diagnosis для {car_id}: {error}")

    try:
        r = requests.get(INSPECTION_URL.format(id=car_id), headers=HEADERS, timeout=20)
        if r.ok:
            result["inspection"] = r.json()
    except Exception as error:
        logger.warning(f"Не удалось получить inspection для {car_id}: {error}")

    return result


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    summary = {}

    for model_label, generation_buckets in SEARCHES.items():
        matches = []
        seen_ids = set()
        total_raw = 0

        for manufacturer, model in generation_buckets:
            try:
                results = search_model_all(manufacturer, model)
            except Exception as error:
                logger.error(f"[{model_label}] не удалось искать {manufacturer}/{model}: {error}")
                continue

            total_raw += len(results)
            logger.info(f"[{model_label}] {manufacturer}/{model}: просмотрено {len(results)} объявлений")
            for item in results:
                if item["Id"] in seen_ids:
                    continue
                if matches_filters(item):
                    seen_ids.add(item["Id"])
                    matches.append(item)

        logger.info(f"[{model_label}] всего просмотрено {total_raw}, подходит под фильтры {len(matches)}")
        summary[model_label] = []

        for i, item in enumerate(matches, start=1):
            car_id = item["Id"]
            logger.info(f"[{model_label}] ({i}/{len(matches)}) детали id={car_id}")

            photos = fetch_full_photos(car_id)
            extra = fetch_diagnosis_and_inspection(car_id)

            record = {
                "model_label": model_label,
                "source": "encar.com",
                **item,
                "photos": photos,
                **extra,
            }

            out_path = OUTPUT_DIR / f"encar_{car_id}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Сохранено: {out_path.name} (фото: {len(photos)})")

            summary[model_label].append(record)
            time.sleep(0.5)

    logger.info("=== Готово ===")
    for model_label, records in summary.items():
        logger.info(f"{model_label}: сохранено {len(records)} объявлений в {OUTPUT_DIR}")


if __name__ == "__main__":
    run()
