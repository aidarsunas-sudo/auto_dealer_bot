# -*- coding: utf-8 -*-
"""
charancha_scraper.py — боевой модуль для Charancha (charancha.com),
экспортной платформы б/у авто (Deutsche Auto Mobil Group).

Используется в orchestrator.py (автоматика по расписанию) — сайт полностью
публичный, логин не нужен вообще.

Марка/модель на Charancha — числовые коды (makerCode/modelCode), НЕ
свободный текст. Резолвинг — через чистый JSON API (без Playwright):
    GET svc.charancha.com/search/cars/buy-cars/search/filter
        (без параметров)               -> список марок с кодами
    GET .../search/filter?makerCode=041 -> список моделей ЭТОЙ марки с кодами
Результат резолвинга кэшируется через target_model_resolver (один раз на
новую пару бренд+модель, а не на каждый прогон).
"""

import logging
import time

import requests

from brand_names_ko import to_korean, model_to_korean, from_korean
from engine_filter import engine_fuel_hint, FUEL_NAME_KO
from model_specs import find_spec
from target_model_resolver import resolve

logger = logging.getLogger("charancha_scraper")


def _manufacture_year(item: dict) -> int | None:
    """Обратная совместимость — только год, см. _manufacture_year_month()."""
    return _manufacture_year_month(item)[0]


def _manufacture_year_month(item: dict) -> tuple:
    """
    Год И МЕСЯЦ выпуска для расчёта таможенного возраста (age_calculator.py:
    месяц важен у границы 3/5/7 лет, см. его докстринг) — НЕ item["releaseYear"]!
    Это корейский маркетинговый "модельный год" (расходится с реальной датой
    почти в половине объявлений — проверено вживую: 51 из 100 в выборке
    Kia K3). Настоящая дата — item["firstRegisteredAt"] (дата первой
    регистрации, формат ГГГГММ или ГГГГММДД — оба варианта встречаются) —
    год из первых 4 символов, месяц из следующих 2 (если строка достаточно
    длинная — при ГГГГ-only варианте месяц остаётся None).

    Returns:
        (year, month) — month может быть None (releaseYear-фолбэк без
        месяца, или firstRegisteredAt короче ожидаемого) — age_calculator.py
        сам подставит консервативный дефолт в этом случае.
    """
    first_registered = item.get("firstRegisteredAt")
    if first_registered:
        digits = str(first_registered)
        year_str = digits[:4]
        if year_str.isdigit():
            year = int(year_str)
            month_str = digits[4:6]
            month = int(month_str) if month_str.isdigit() and 1 <= int(month_str) <= 12 else None
            return year, month

    release_year = item.get("releaseYear")
    if release_year and str(release_year).isdigit():
        return int(release_year), None
    return None, None


FILTER_URL = "https://svc.charancha.com/search/cars/buy-cars/search/filter"
SEARCH_URL = "https://svc.charancha.com/search/cars/buy-cars/search/cursor"
PAGE_SIZE = 30
MAX_PAGES = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _lookup_codes(brand: str, model: str) -> dict | None:
    """Ищет makerCode/modelCode на сайте (см. докстринг файла) — вызывается
    resolve() только при промахе кэша, не на каждый запрос."""
    brand_ko = to_korean(brand)

    resp = requests.get(FILTER_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    maker_group = next(g for g in resp.json()["data"] if g["parameterKey"] == "makerCode")

    maker_match = next((m for m in maker_group["items"] if m["name"] == brand_ko), None)
    if maker_match is None:
        return None
    maker_code = maker_match["code"]

    resp2 = requests.get(FILTER_URL, params={"makerCode": maker_code}, headers=HEADERS, timeout=20)
    resp2.raise_for_status()
    model_group = next(g for g in resp2.json()["data"] if g["parameterKey"] == "modelCode")

    # Модели-аббревиатуры (K3, Q3, X6) обычно хранятся латиницей как есть,
    # но многие "обычные" названия — корейской транслитерацией (Tiguan ->
    # 티구안) — пробуем оба варианта.
    candidates = [model.strip().lower(), model_to_korean(model).lower()]
    model_match = next(
        (m for m in model_group["items"] if any(c in m["name"].lower() for c in candidates)),
        None,
    )
    if model_match is None:
        return None

    return {"makerCode": maker_code, "modelCode": model_match["code"]}


def _normalize_item(item: dict, target: dict) -> dict:
    image = item.get("image") or {}
    raw_title = f"{item.get('makerName', '')} {item.get('modelName', '')} {item.get('modelDetailName', '')}".strip()
    # find_spec ищет по латинским подстрокам ("tiguan", "tdi", "q3") — сайт
    # часто отдаёт название модели корейской транслитерацией (напр. "티구안"
    # вместо "Tiguan"), поэтому для поиска спеки подставляем канонический
    # английский brand/model/engine из target_models.json впереди raw_title
    # (для trim в отчётах используем raw_title как есть, без этой подстановки).
    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {raw_title}"

    cc_from_site = item.get("displacement")
    year_int, month_int = _manufacture_year_month(item)

    spec = find_spec(title_for_spec, year_int)
    cc = cc_from_site or (spec.cc if spec else None)
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("carId")

    return {
        "brand": target["brand"],
        "model": target["model"],
        # gradeDetailName — обычно нужный человекочитаемый бэдж комплектации.
        # РАНЬШЕ при его отсутствии сюда падал raw_title (makerName+
        # modelName+modelDetailName с сайта) — а raw_title почти всегда
        # содержит корейский текст (напр. "르노코리아(삼성) QM6 더 뉴 QM6"),
        # который валит validate_post_before_publish()/prepare_post_fields()
        # на проверке "корейские символы в названии" — ИЗ-ЗА ЭТОГО ни один
        # targeted-кандидат с charancha не публиковался (найдено 2026-07-20:
        # 100% отказ на "нет читаемого названия"). brand/model уже даны
        # каноническим английским текстом из target_models.json — trim без
        # чистого бэджа лучше оставить пустым, чем протащить корейский.
        "trim": item.get("gradeDetailName") or "",
        "external_id": external_id,
        "year": year_int,
        "month": month_int,
        "mileage_km": item.get("mileage"),
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": (item.get("sellPrice") or 0) * 10000,  # 만원 -> вон
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": f"https://www.charancha.com/cars/{external_id}",
        # доп. поля для отчётов (model_search.py) — save_car их не читает
        "source": "charancha",
        "photos": image.get("images") or [],
        "transmission": item.get("transmissionName"),
        "is_accident": item.get("isAccident"),
        "is_seizure": item.get("isSeizure"),
        "color": item.get("colorName"),
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на Charancha по целевой модели (см. target_models.json).
    Возвращает список, совместимый с database.save_car (+ доп. поля).
    """
    codes = resolve("charancha", target["brand"], target["model"], _lookup_codes, log)
    if codes is None:
        return []

    all_items = []
    cursor = None

    for _ in range(MAX_PAGES):
        params = {
            "size": PAGE_SIZE,
            "makerCode": codes["makerCode"],
            "modelCode": codes["modelCode"],
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"charancha: ошибка запроса для {target['brand']} {target['model']}: {error}")
            break

        items = data.get("data", [])
        if not items:
            break

        fuel_hint = engine_fuel_hint(target.get("engine"))

        for item in items:
            if fuel_hint is not None and item.get("fuelName") != FUEL_NAME_KO[fuel_hint]:
                continue  # явно другое топливо (например, бензин вместо запрошенного TDI-дизеля)

            normalized = _normalize_item(item, target)
            year = normalized.get("year")
            mileage_km = normalized.get("mileage_km")
            if year is not None and not (target.get("year_from", 0) <= year <= target.get("year_to", 9999)):
                continue
            if target.get("mileage_max") is not None and mileage_km is not None and mileage_km > target["mileage_max"]:
                continue
            all_items.append(normalized)

        if not data.get("hasNext"):
            break
        cursor = data.get("nextCursor")
        time.sleep(0.3)

    log.info(f"charancha: {target['brand']} {target['model']} -> найдено {len(all_items)} (после фильтров)")
    return all_items


def _normalize_broad_item(item: dict) -> dict:
    """
    Как _normalize_item(), но БЕЗ фиксированного target — brand/model берём
    из самих данных площадки (для broad-режима, см. broad_collector.py), а
    не из target_models.json (там его просто нет).
    """
    image = item.get("image") or {}
    raw_title = f"{item.get('makerName', '')} {item.get('modelName', '')} {item.get('modelDetailName', '')}".strip()

    cc_from_site = item.get("displacement")
    year_int, month_int = _manufacture_year_month(item)

    spec = find_spec(raw_title, year_int)
    cc = cc_from_site or (spec.cc if spec else None)
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("carId")

    # item["modelName"] часто по-корейски (напр. "아반떼" вместо "Avante") —
    # from_korean() переводит только БРЕНД, модель остаётся сырым текстом.
    # Раз hp уже найден (find_spec совпал) — используем человекочитаемое имя
    # spec.label (напр. "Hyundai Avante 1.6 Gasoline", УЖЕ включает марку)
    # вместо него (тот же баг, что был у kbchachacha/Encar/OnBid broad-режима
    # — см. FINAL_STATE.md, найдено на реальном примере 2026-07-06); brand/
    # trim в этом случае очищаются, чтобы марка не задвоилась.
    brand_display = "" if spec else from_korean(item.get("makerName", ""))
    model_display = spec.label if spec else item.get("modelName", "")
    trim_display = "" if spec else (item.get("gradeDetailName") or raw_title)

    return {
        "brand": brand_display,
        "model": model_display,
        "trim": trim_display,
        "external_id": external_id,
        "year": year_int,
        "month": month_int,
        "mileage_km": item.get("mileage"),
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": (item.get("sellPrice") or 0) * 10000,  # 만원 -> вон
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": f"https://www.charancha.com/cars/{external_id}",
        "source": "charancha",
        "photos": image.get("images") or [],
        "transmission": item.get("transmissionName"),
        "is_accident": item.get("isAccident"),
        "is_seizure": item.get("isSeizure"),
        "color": item.get("colorName"),
    }


def browse_broad(config: dict, log: logging.Logger) -> list:
    """
    Широкий поиск (Режим 2, см. broad_collector.py) — БЕЗ привязки к
    конкретной марке/модели, только фильтр по max_hp (+ опционально
    fuel_types/year_from/mileage_max из broad_search_config.json).

    hp по-прежнему берётся только через model_specs.find_spec() (Charancha
    его не отдаёт напрямую) — машины с неопределённым hp здесь ОТБРАСЫВАЮТСЯ
    сразу (в отличие от targeted-режима, где допустимо запостить машину без
    hp и просто пропустить расчёт цены): смысл broad-режима — заведомо
    подтверждённая льгота по утильсбору, гадать нельзя.
    """
    max_hp = config.get("max_hp")
    max_pages = config.get("max_pages_per_source", 5)

    all_items = []
    cursor = None

    for _ in range(max_pages):
        params = {"size": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"charancha (broad): ошибка запроса: {error}")
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            normalized = _normalize_broad_item(item)

            if normalized.get("hp") is None:
                continue  # не смогли подтвердить мощность — не рискуем
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

        if not data.get("hasNext"):
            break
        cursor = data.get("nextCursor")
        time.sleep(0.3)

    log.info(f"charancha (broad): найдено {len(all_items)} машин с hp<={max_hp}")
    return all_items
