# -*- coding: utf-8 -*-
"""
encar_scraper.py — боевой модуль для Encar (encar.com), крупнейшей площадки
б/у авто в Корее.

Используется в orchestrator.py (автоматика по расписанию) — сайт полностью
публичный, логин не нужен вообще.

Марка/модель на Encar — тоже текстовые значения (не числовые коды, в
отличие от kbchachacha), но сам список моделей марки ("ModelGroup") нигде не
отдаётся чистым JSON-API — он встроен в HTML страницы поиска и раскрывается
только после клика по марке в каскадном фильтре (см. разведку: DOM после
клика на "제조사" -> марка содержит <a data-action="...ModelGroup.K3...">K3</a>
для каждой модели этой марки сразу, без дополнительных кликов). Поэтому
резолвинг модели требует Playwright, а сам поиск объявлений после этого —
уже чистый JSON REST (api.encar.com/search/car/list/general), Playwright не
нужен.

ВАЖНО: в отличие от kbchachacha, для запроса списка объявлений не нужны
числовые коды марки/модели — сам текстовый q-запрос вида
"(And.Hidden.N._.CarType.Y._.Manufacturer.기아._.ModelGroup.K3.)" уже
достаточен (коды mnfccd/mdlgrpcd нужны сайту только для UI, не для API).
ModelGroup объединяет ВСЕ поколения модели сразу — фильтрация по годам и
пробегу делается на нашей стороне (как во всех остальных адаптерах), без
необходимости резолвить отдельные generation-коды, как на kbchachacha.

CarType: "Y" для отечественных брендов (현대/기아/제네시스/쉐보레(GM대우)/
르노코리아(삼성)/KG모빌리티(쌍용)), иначе "A" (импорт) — подтверждено вживую
(Y работает для 기아, даёт 0 для импортных марок; A работает для аудио/фольксвагена).
"""

import logging
import re
import time

import requests
from playwright.sync_api import sync_playwright

from brand_names_ko import to_korean, model_to_korean, from_korean
from engine_filter import engine_fuel_hint, FUEL_NAME_KO
from model_specs import find_spec
from target_model_resolver import resolve

logger = logging.getLogger("encar_scraper")

SEARCH_URL = "https://api.encar.com/search/car/list/general"
DOMESTIC_LIST_URL = "https://www.encar.com/dc/dc_carsearchlist.do?carType=kor"
IMPORT_LIST_URL = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for"
PHOTO_CDN_BASE = "https://ci.encar.com"
DETAIL_URL_TEMPLATE = "https://fem.encar.com/cars/detail/{id}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

PAGE_SIZE = 200
MAX_PAGES = 15  # защитный потолок (PAGE_SIZE * MAX_PAGES = 3000 объявлений)

DOMESTIC_BRANDS_KO = {
    "현대", "기아", "제네시스", "쉐보레(GM대우)", "르노코리아(삼성)", "KG모빌리티(쌍용)",
}


def _car_type(brand_ko: str) -> str:
    return "Y" if brand_ko in DOMESTIC_BRANDS_KO else "A"


def _lookup_codes(brand: str, model: str) -> dict | None:
    """
    Резолвит CarType + точный текст ModelGroup через Playwright (см.
    докстринг файла — вызывается только при промахе кэша). Числовые коды
    mnfccd/mdlgrpcd сайту нужны только для UI, для самого API-запроса
    достаточно текстовых значений Manufacturer/ModelGroup.
    """
    brand_ko = to_korean(brand)
    car_type = _car_type(brand_ko)
    list_url = DOMESTIC_LIST_URL if car_type == "Y" else IMPORT_LIST_URL
    model_candidates = [model.strip().lower(), model_to_korean(model).lower()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)
            page.click("text=제조사", timeout=8000)
            time.sleep(1.5)
            page.click(f"text={brand_ko}", timeout=8000)
            time.sleep(2)
        except Exception as error:
            logger.warning(f"encar resolver: не удалось открыть марку {brand_ko}: {error}")
            browser.close()
            return None

        model_links = page.eval_on_selector_all(
            "a[data-action*='ModelGroup.']",
            "els => els.map(e => e.innerText.trim())",
        )
        browser.close()

    model_match = next(
        (text for text in model_links if any(cand in text.lower() for cand in model_candidates)),
        None,
    )
    if model_match is None:
        return None

    return {"car_type": car_type, "manufacturer_ko": brand_ko, "model_group": model_match}


def _extract_photos(item: dict) -> list:
    """
    Список поиска отдаёт item["Photos"] — уже полный массив фото объявления
    (не только "витринные" 4, как было в раннем тестовом скрипте) — доп.
    запрос на детальную карточку не нужен.
    """
    return [
        f"{PHOTO_CDN_BASE}{p['location']}"
        for p in (item.get("Photos") or [])
        if p.get("location")
    ]


def _normalize_item(item: dict, target: dict) -> dict:
    manufacturer = item.get("Manufacturer", "")
    model_text = item.get("Model", "")
    badge = item.get("Badge", "")
    # find_spec ищет по латинским подстрокам ("tiguan", "tdi", "q3") — сайт
    # часто отдаёт название модели корейской транслитерацией (напр. "티구안"
    # вместо "Tiguan"), поэтому подставляем канонический английский
    # brand/model/engine из target_models.json впереди, а сырой текст с
    # сайта (badge часто содержит "TDI"/"GT" латиницей) — для уточнения.
    raw_title = f"{manufacturer} {model_text} {badge}".strip()
    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {raw_title}"

    form_year = item.get("FormYear")
    year_int = int(form_year) if form_year and str(form_year).isdigit() else None

    mileage = item.get("Mileage")
    mileage_km = int(mileage) if mileage is not None else None

    spec = find_spec(title_for_spec, year_int)
    cc = spec.cc if spec else None
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("Id")
    photos = _extract_photos(item)

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": badge or raw_title,
        "external_id": external_id,
        "year": year_int,
        "mileage_km": mileage_km,
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": (item.get("Price") or 0) * 10000,  # 만원 -> вон
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": DETAIL_URL_TEMPLATE.format(id=external_id),
        # доп. поля для отчётов (model_search.py) — save_car их не читает
        "source": "encar",
        "photos": photos,
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на Encar по целевой модели (см. target_models.json).
    Возвращает список, совместимый с database.save_car (+ доп. поля).
    """
    codes = resolve("encar", target["brand"], target["model"], _lookup_codes, log)
    if codes is None:
        return []

    q = (
        f"(And.Hidden.N._.CarType.{codes['car_type']}._.Manufacturer.{codes['manufacturer_ko']}."
        f"_.ModelGroup.{codes['model_group']}.)"
    )
    fuel_hint = engine_fuel_hint(target.get("engine"))

    all_items = []
    offset = 0
    for _ in range(MAX_PAGES):
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"count": "true", "q": q, "sr": f"|ModifiedDate|{offset}|{PAGE_SIZE}"},
                headers=HEADERS, timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"encar: ошибка запроса для {target['brand']} {target['model']}: {error}")
            break

        results = data.get("SearchResults", [])
        if not results:
            break

        for item in results:
            if fuel_hint is not None and item.get("FuelType") != FUEL_NAME_KO[fuel_hint]:
                continue  # явно другое топливо (например, бензин вместо запрошенного TDI-дизеля)

            normalized = _normalize_item(item, target)
            year = normalized.get("year")
            mileage_km = normalized.get("mileage_km")
            if year is not None and not (target.get("year_from", 0) <= year <= target.get("year_to", 9999)):
                continue
            if target.get("mileage_max") is not None and mileage_km is not None and mileage_km > target["mileage_max"]:
                continue
            all_items.append(normalized)

        offset += PAGE_SIZE
        if offset >= data.get("Count", 0):
            break
        time.sleep(0.3)

    log.info(f"encar: {target['brand']} {target['model']} -> найдено {len(all_items)} (после фильтров)")
    return all_items


def _normalize_broad_item(item: dict) -> dict:
    """Как _normalize_item(), но без фиксированного target — brand/model
    берём из самих данных площадки (см. broad_collector.py)."""
    manufacturer = item.get("Manufacturer", "")
    model_text = item.get("Model", "")
    badge = item.get("Badge", "")
    raw_title = f"{manufacturer} {model_text} {badge}".strip()

    form_year = item.get("FormYear")
    year_int = int(form_year) if form_year and str(form_year).isdigit() else None

    mileage = item.get("Mileage")
    mileage_km = int(mileage) if mileage is not None else None

    spec = find_spec(raw_title, year_int)
    cc = spec.cc if spec else None
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("Id")
    photos = _extract_photos(item)

    return {
        "brand": from_korean(manufacturer),
        "model": model_text,
        "trim": badge or raw_title,
        "external_id": external_id,
        "year": year_int,
        "mileage_km": mileage_km,
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": (item.get("Price") or 0) * 10000,  # 만원 -> вон
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": DETAIL_URL_TEMPLATE.format(id=external_id),
        "source": "encar",
        "photos": photos,
    }


def browse_broad(config: dict, log: logging.Logger) -> list:
    """
    Широкий поиск (Режим 2, см. broad_collector.py) — БЕЗ марки/модели,
    просто "(And.Hidden.N.)" (подтверждено вживую: работает, ~220k записей
    суммарно и по отечественным, и по импортным маркам сразу — CarType
    указывать не обязательно). Фильтр по max_hp (+ опционально
    fuel_types/year_from/mileage_max) — см. docstring charancha_scraper.
    browse_broad для объяснения, почему машины с неопределённым hp
    отбрасываются, а не пропускаются с пустым hp.
    """
    max_hp = config.get("max_hp")
    max_pages = config.get("max_pages_per_source", 5)

    all_items = []
    offset = 0
    for _ in range(max_pages):
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"count": "true", "q": "(And.Hidden.N.)", "sr": f"|ModifiedDate|{offset}|{PAGE_SIZE}"},
                headers=HEADERS, timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"encar (broad): ошибка запроса: {error}")
            break

        results = data.get("SearchResults", [])
        if not results:
            break

        for item in results:
            normalized = _normalize_broad_item(item)

            if normalized.get("hp") is None:
                continue
            if max_hp is not None and normalized["hp"] > max_hp:
                continue

            year = normalized.get("year")
            if config.get("year_from") is not None and year is not None and year < config["year_from"]:
                continue
            mileage_km = normalized.get("mileage_km")
            if config.get("mileage_max") is not None and mileage_km is not None and mileage_km > config["mileage_max"]:
                continue
            fuel_types = config.get("fuel_types")
            if fuel_types is not None and normalized.get("fuel_code") not in fuel_types:
                continue

            all_items.append(normalized)

        offset += PAGE_SIZE
        if offset >= data.get("Count", 0):
            break
        time.sleep(0.3)

    log.info(f"encar (broad): найдено {len(all_items)} машин с hp<={max_hp}")
    return all_items
