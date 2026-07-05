# -*- coding: utf-8 -*-
"""
charancha_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает
список объявлений с Charancha (charancha.com) — экспортной платформы
б/у авто от Deutsche Auto Mobil Group.

САМЫЙ ПРОСТОЙ источник из всех, что разбирали: НЕ требует логина вообще
и отдаёт чистый JSON без единого обращения к Playwright.

Что уже подтверждено в разведке:
- Сайт сам по себе — Next.js (App Router, RSC). Страница /cars
  использует `_rsc=`-запросы, которые только рендерят HTML-обвязку и
  НЕ содержат данных о машинах — это подвох, реальные данные не оттуда.
- Настоящие данные — на ОТДЕЛЬНОМ поддомене svc.charancha.com (видимо,
  "service", отдельный backend API), доступном без всякой авторизации:
  GET https://svc.charancha.com/search/cars/buy-cars/search/cursor?size=N&cursor=...
  Ответ: {"data": [...машины...], "nextCursor": "...", "hasNext": bool}
  Пагинация курсорная — nextCursor из ответа передаётся в следующий запрос.
- Каждая машина уже содержит ПОЛНУЮ галерею фото (image.images — список
  URL, в примерах доходило до ~19 фото) и признаки состояния (isAccident,
  isSeizure, isMortgage — в разведанных примерах были null, возможно
  заполняются не для всех объявлений или требуют доп. вызова —
  не проверяли специально, отслеживай "не работает как ожидалось").

Запуск:
    python charancha_test_scraper.py
"""

import json
import logging
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("charancha_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_charancha"

SEARCH_URL = "https://svc.charancha.com/search/cars/buy-cars/search/cursor"
PAGE_SIZE = 30
MAX_PAGES = 5  # тестовый масштаб

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def normalize_item(item: dict) -> dict:
    image = item.get("image") or {}
    return {
        "car_id": item.get("carId"),
        "status": item.get("statusName"),
        "title": f"{item.get('makerName', '')} {item.get('modelName', '')} {item.get('modelDetailName', '')}".strip(),
        "plate": item.get("carNumber"),
        "vin": item.get("carIdentificationNumber"),
        "maker": item.get("makerName"),
        "model": item.get("modelName"),
        "model_detail": item.get("modelDetailName"),
        "grade": item.get("gradeDetailName"),
        "year": item.get("releaseYear"),
        "first_registered_at": item.get("firstRegisteredAt"),
        "mileage_km": item.get("mileage"),
        "fuel": item.get("fuelName"),
        "transmission": item.get("transmissionName"),
        "color": item.get("colorName"),
        "region": item.get("regionName"),
        "price_krw": (item.get("sellPrice") or 0) * 10000,  # 만원 -> вон
        "option_count": item.get("optionCount"),
        "option_names": item.get("optionNames"),
        "is_accident": item.get("isAccident"),
        "is_seizure": item.get("isSeizure"),
        "is_mortgage": item.get("isMortgage"),
        "is_partner": item.get("isPartner"),
        "dealer_name": (item.get("dealer") or {}).get("name"),
        "photos": image.get("images") or [],
        "main_photo": image.get("representativeImage"),
    }


def search_all() -> list:
    all_items = []
    cursor = None

    for page_num in range(1, MAX_PAGES + 1):
        params = {"size": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            logger.error(f"Страница {page_num}: ошибка запроса ({error})")
            break

        items = data.get("data", [])
        if not items:
            logger.info(f"Страница {page_num}: пусто, останавливаюсь")
            break

        all_items.extend(normalize_item(item) for item in items)
        logger.info(f"Страница {page_num}: получено {len(items)}")

        if not data.get("hasNext"):
            logger.info("hasNext=False, это последняя страница")
            break

        cursor = data.get("nextCursor")
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    items = search_all()

    out_path = OUTPUT_DIR / "charancha_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    photo_counts = [len(it["photos"]) for it in items]
    avg_photos = sum(photo_counts) / len(photo_counts) if photo_counts else 0
    logger.info(f"=== Готово: сохранено {len(items)} машин в {out_path} (в среднем {avg_photos:.0f} фото на машину) ===")


if __name__ == "__main__":
    run()
