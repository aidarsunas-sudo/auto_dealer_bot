# -*- coding: utf-8 -*-
"""
kbchachacha_target_adapter.py — тонкая обёртка над уже существующим
used_car_scraper.py для поиска по target_models.json на kbchachacha.

Используется в orchestrator.py (автоматика по расписанию) — сайт публичный,
логин не нужен вообще.

Марка/модель на kbchachacha — числовые коды (makerCode/classCode/carCode),
раскрывающиеся ТОЛЬКО через клики по каскадным фильтрам сайта (нет чистого
JSON-API для этого, в отличие от Charancha) — резолвинг требует Playwright.
Само получение списка объявлений после резолвинга — уже существующий,
чистый requests-based used_car_scraper.parse_all_pages(), Playwright для
этого не нужен.

Резолвинг кэшируется через target_model_resolver — Playwright запускается
только для НОВОЙ пары бренд+модель, не при каждом прогоне.

ВАЖНО (найдено вживую): параметр countryOrder в запросе списка ЛОМАЕТ
результат (возвращает 0 объявлений, хотя коды верны) — используем только
makerCode/classCode/carCode/regiDay, без countryOrder.
"""

import logging
import re
import time

import requests
from playwright.sync_api import sync_playwright

from brand_names_ko import to_korean, model_to_korean
from engine_filter import engine_fuel_hint
from target_model_resolver import resolve
from used_car_scraper import parse_all_pages

logger = logging.getLogger("kbchachacha_target_adapter")

SEARCH_PAGE_URL = "https://www.kbchachacha.com/public/search/main.kbc"
MAKER_LIST_URL = "https://www.kbchachacha.com/public/search/carMaker.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _lookup_maker_code(brand_ko: str) -> int | None:
    """makerCode — через простой JSON-эндпоинт (без Playwright), см. разведку
    прошлой сессии: /public/search/carMaker.json отдаёт марки списками
    '국산' (домашние) и '수입' (импорт), каждая — {makerName, makerCode, ...}."""
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": SEARCH_PAGE_URL,
    }
    resp = requests.get(MAKER_LIST_URL, params={"page": 1}, headers=headers, timeout=20)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    for group_key in ("국산", "수입"):
        for maker in result.get(group_key, []):
            if maker.get("makerName") == brand_ko:
                return int(maker["makerCode"])
    return None


def _parse_generation_years(label: str) -> tuple:
    """
    Парсит диапазон лет поколения из текста вида "더 뉴 K3(BD) (21년~현재)"
    или "올 뉴 K3 (18년~21년)" -> (2021, 9999) или (2018, 2021).
    Возвращает (None, None), если не удалось распознать.
    """
    m = re.search(r"\((\d{2})년\s*~\s*(현재|\d{2})년?\)", label)
    if not m:
        return None, None
    start = 2000 + int(m.group(1))
    end = 9999 if m.group(2) == "현재" else 2000 + int(m.group(2))
    return start, end


def _lookup_codes(brand: str, model: str) -> dict | None:
    """
    Резолвит makerCode/classCode/список подходящих carCode через Playwright.
    Сайт нередко "спотыкается" на первом клике (перекрывающий баннер/оверлей
    ещё не исчез) — отрицательный результат в этом случае кэшируется на 7
    дней (see target_model_resolver), поэтому даём себе пару повторных
    попыток на чисто технические сбои, прежде чем сдаться.
    """
    for attempt in range(1, 3):
        result = _lookup_codes_once(brand, model)
        if result is not None:
            return result
        logger.warning(f"kbchachacha resolver: попытка {attempt}/2 не удалась для {brand} {model}")
    return None


def _lookup_codes_once(brand: str, model: str) -> dict | None:
    """
    Резолвит makerCode/classCode/список подходящих carCode через Playwright
    (см. докстринг файла — вызывается только при промахе кэша).

    Возвращает {"makerCode": int, "classCode": int, "car_codes": [{"code": int,
    "year_from": int, "year_to": int}, ...]} — по одному элементу на каждое
    найденное поколение модели (может быть несколько, если название модели
    неоднозначно matches несколько generations).
    """
    brand_ko = to_korean(brand)
    model_candidates = [model.strip().lower(), model_to_korean(model).lower()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        try:
            page.goto(SEARCH_PAGE_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: goto warning: {error}")
        time.sleep(2)

        try:
            page.click("text=차종", timeout=8000)
            time.sleep(1.5)
            page.click(f"text={brand_ko}", timeout=8000)
            time.sleep(1.5)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: не удалось открыть марку {brand_ko}: {error}")
            browser.close()
            return None

        # Ищем ссылку на classCode, чей видимый текст совпадает с моделью
        # (латиницей или корейской транслитерацией).
        class_links = page.eval_on_selector_all(
            "a[href*='setClassCode']",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.getAttribute('href')}))",
        )
        class_match = next(
            (c for c in class_links if any(cand in c["text"].lower() for cand in model_candidates)),
            None,
        )
        if class_match is None:
            browser.close()
            return None

        class_code_m = re.search(r"setClassCode\('(\d+)'\)", class_match["href"])
        if not class_code_m:
            browser.close()
            return None
        class_code = int(class_code_m.group(1))

        try:
            page.click(f"a[href*=\"setClassCode('{class_code}')\"]", timeout=8000)
            time.sleep(2)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: не удалось выбрать классCode {class_code}: {error}")
            browser.close()
            return None

        car_items = page.eval_on_selector_all(
            "input[id^='carCode-']",
            """els => els.map(e => {
                const label = document.querySelector(`label[for="${e.id}"]`);
                return {value: e.value, text: label ? label.innerText.trim() : ''};
            })""",
        )
        browser.close()

    maker_code = _lookup_maker_code(brand_ko)
    if maker_code is None:
        logger.warning(f"kbchachacha resolver: не удалось определить makerCode для {brand_ko}")
        return None

    car_codes = []
    for item in car_items:
        year_from, year_to = _parse_generation_years(item["text"])
        car_codes.append({
            "code": int(item["value"]),
            "text": item["text"],
            "year_from": year_from,
            "year_to": year_to,
        })

    if not car_codes:
        return None

    return {"makerCode": maker_code, "classCode": class_code, "car_codes": car_codes}


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на kbchachacha по целевой модели. Возвращает список,
    совместимый с database.save_car (+ доп. поля для отчётов).
    """
    codes = resolve("kbchachacha", target["brand"], target["model"], _lookup_codes, log)
    if codes is None:
        return []

    year_from = target.get("year_from", 0)
    year_to = target.get("year_to", 9999)

    # Берём только те поколения (carCode), чей диапазон лет пересекается с
    # запрошенным — модель может быть неоднозначна (несколько generations).
    matching_car_codes = [
        c["code"] for c in codes["car_codes"]
        if c["year_from"] is None or (c["year_from"] <= year_to and c["year_to"] >= year_from)
    ]
    if not matching_car_codes:
        matching_car_codes = [c["code"] for c in codes["car_codes"]]  # честно берём все, если не распознали года

    fuel_hint = engine_fuel_hint(target.get("engine"))

    all_cars = []
    for car_code in matching_car_codes:
        search_params = {
            "makerCode": codes["makerCode"],
            "classCode": codes["classCode"],
            "carCode": car_code,
        }
        try:
            cars = parse_all_pages(search_params, max_pages=15, delay=0.5)
        except Exception as error:
            log.error(f"kbchachacha: ошибка поиска для {target['brand']} {target['model']} "
                      f"(carCode={car_code}): {error}")
            continue

        for car in cars:
            if car.get("year") is not None and not (year_from <= car["year"] <= year_to):
                continue
            if target.get("mileage_max") is not None and car.get("mileage_km") is not None \
                    and car["mileage_km"] > target["mileage_max"]:
                continue
            if fuel_hint is not None and car.get("fuel_code") != fuel_hint:
                # На kbchachacha fuel_code берётся ТОЛЬКО из model_specs.py
                # (сырого поля топлива в списке объявлений нет) — если
                # запрошен конкретный мотор (engine в target_models.json), а
                # спека не нашлась (fuel_code is None, напр. для смежного
                # варианта кузова вроде Tiguan Allspace на TSI под тем же
                # classCode), это тоже "не совпадает", а не "неизвестно,
                # пропускаем" — иначе в отчёт/базу попадают чужие моторы.
                continue

            # used_car_scraper.parse_all_pages() кладёт полное описание
            # (марка+модель+комплектация с сайта) в car["model"], а
            # car["trim"] у него — просто car_seq (числовой ID, для
            # уникальности в старой схеме WATCHLIST/USED_CAR_SEARCHES). Для
            # отображения в посте нужно ровно наоборот: общее имя модели —
            # из target_models.json, а подробности с сайта — как раз в
            # trim. Сохраняем ДО перезаписи car["model"].
            car["trim"] = car["model"]
            car["brand"] = target["brand"]
            car["model"] = target["model"]
            car["source"] = "kbchachacha"
            all_cars.append(car)

    # Один и тот же физический автомобиль может попасться дважды, если два
    # соседних поколения (carCode) пересекаются по годам и сайт по ошибке
    # относит объявление к обоим — дедуп по external_id (car_seq) на выходе.
    seen_ids = set()
    deduped = []
    for car in all_cars:
        external_id = car.get("external_id")
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)
        deduped.append(car)

    log.info(f"kbchachacha: {target['brand']} {target['model']} -> найдено {len(deduped)} (после фильтров)")
    return deduped
